from __future__ import annotations

import csv
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
DEFAULT_POLL_MS = 10
HEARTBEAT_SECONDS = 1.0
WATCHER_SECONDS = 1.0
SHAHEEN_CPU_TDP_WATTS = 360.0


class NullGpuSampler:
    status = "disabled on Shaheen"

    def sample(self):
        return None


class Worker:
    def __init__(self, session_dir: Path, repo_root: Path, poll_ms: int):
        self.session_dir = session_dir
        self.repo_root = repo_root
        self.poll_seconds = max(0.001, poll_ms / 1000.0)
        self.node_id = os.environ.get("SLURMD_NODENAME") or socket.gethostname()
        self.hostname = socket.gethostname()
        self.node_path = self.session_dir / "nodes" / f"{self.node_id}.json"
        self.sample_path = self.session_dir / "samples" / f"{self.node_id}.csv"
        self.log_dir = self.session_dir / "logs"
        self.status = "initializing"
        self.message = ""
        self.hw_info = collect_hw_info()
        self.latest_power: dict[str, object] | None = None
        self.last_sequence = 0
        self.burner: subprocess.Popen[str] | None = None
        self.burner_job_sequence: int | None = None
        self.burner_start_at: float | None = None
        self._stop = False
        self._sampler = self._build_sampler()

    def run(self) -> int:
        self._install_signal_handlers()
        self._prepare_dirs()
        self._write_sample_header()
        self.status = "ready"
        self._write_state()

        next_heartbeat = 0.0
        next_sample = 0.0
        while not self._stop:
            now = time.monotonic()
            self._handle_command()
            self._poll_burner()
            if now >= next_sample:
                self._sample_power()
                next_sample = now + WATCHER_SECONDS
            if now >= next_heartbeat:
                self._write_state()
                next_heartbeat = now + HEARTBEAT_SECONDS
            time.sleep(self.poll_seconds)

        self._stop_burner()
        self.status = "stopped"
        self._write_state()
        return 0

    def _prepare_dirs(self) -> None:
        for relative in ("nodes", "samples", "logs"):
            (self.session_dir / relative).mkdir(parents=True, exist_ok=True)

    def _write_sample_header(self) -> None:
        if self.sample_path.exists():
            return
        with self.sample_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "cpu_watts"])

    def _handle_command(self) -> None:
        command_path = self.session_dir / "command.json"
        try:
            command = json.loads(command_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        try:
            sequence = int(command.get("sequence") or 0)
        except (TypeError, ValueError):
            return
        if sequence <= self.last_sequence:
            return

        action = command.get("action")
        if action == "start":
            self._start_burner(command, sequence)
        elif action == "stop":
            self._stop_burner()
            self.status = "ready"
            self.message = ""
        elif action == "release":
            self._stop_burner()
            self._stop = True
        self.last_sequence = sequence
        self._write_state()

    def _start_burner(self, command: dict[str, Any], sequence: int) -> None:
        self._stop_burner()
        start_at = parse_iso_epoch(command.get("start_at"))
        waveform_path = str(command.get("waveform_path") or "")
        duration = str(command.get("duration") or "")
        period = str(command.get("period") or "")
        tick_seconds = str(command.get("tick_seconds") or "0.1")

        if not start_at or not waveform_path or not duration or not period:
            self.status = "error"
            self.message = "invalid start command"
            return

        log_path = self.log_dir / f"{self.node_id}-burner-{sequence}.log"
        command_line = [
            sys.executable,
            str(self.repo_root / "burner"),
            "--cpu",
            "-f",
            waveform_path,
            "-t",
            duration,
            "-p",
            period,
            "--tick",
            tick_seconds,
            "--start",
            str(command["start_at"]),
        ]
        try:
            log_handle = log_path.open("w", encoding="utf-8")
            self.burner = subprocess.Popen(
                command_line,
                cwd=self.repo_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            self.status = "error"
            self.message = f"failed to start burner: {exc}"
            return

        self.burner_job_sequence = sequence
        self.burner_start_at = start_at
        self.status = "arming" if time.time() < start_at else "burning"
        self.message = ""

    def _stop_burner(self) -> None:
        if self.burner is None:
            return
        if self.burner.poll() is None:
            self.burner.terminate()
            try:
                self.burner.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.burner.kill()
                self.burner.wait(timeout=5)
        self.burner = None
        self.burner_job_sequence = None
        self.burner_start_at = None

    def _poll_burner(self) -> None:
        if self.burner is None:
            return
        exit_code = self.burner.poll()
        if exit_code is None:
            if self.burner_start_at is not None and time.time() >= self.burner_start_at:
                self.status = "burning"
            return
        if exit_code == 0:
            self.status = "ready"
            self.message = ""
        else:
            self.status = "error"
            self.message = f"burner exited with {exit_code}"
        self.burner = None
        self.burner_job_sequence = None
        self.burner_start_at = None

    def _sample_power(self) -> None:
        try:
            sample = self._sampler.sample()
        except Exception as exc:
            self.latest_power = {
                "timestamp": iso_now(),
                "cpu_watts": None,
                "status": f"watcher failed: {exc}",
            }
            return

        timestamp = sample.timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        cpu_watts = sample.cpu_watts
        self.latest_power = {
            "timestamp": timestamp,
            "cpu_watts": cpu_watts,
            "status": self._sampler.status,
        }
        with self.sample_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([timestamp, "" if cpu_watts is None else f"{cpu_watts:.6f}"])

    def _write_state(self) -> None:
        payload: dict[str, object] = {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "slurm_node": os.environ.get("SLURMD_NODENAME") or self.node_id,
            "status": self.status,
            "message": self.message,
            "heartbeat_at": iso_now(),
            "hw_info": self.hw_info,
            "burner_pid": self.burner.pid if self.burner is not None else None,
            "command_sequence": self.last_sequence,
            "latest_power": self.latest_power,
        }
        atomic_write_json(self.node_path, payload)

    def _build_sampler(self):
        sys.path.insert(0, str(self.repo_root))
        from warpper.watcher_core import CombinedPowerSampler

        return CombinedPowerSampler(gpu=NullGpuSampler())

    def _install_signal_handlers(self) -> None:
        def handle_signal(signum, frame):
            del signum, frame
            self._stop = True

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)


def collect_hw_info() -> dict[str, object]:
    return {
        "cpu_model": detect_cpu_model(),
        "cpu_count": os.cpu_count() or 0,
        "memory_total_gb": read_memory_total_gb(),
        "ip_address": first_ip_address(),
        "cpu_tdp_watts": detect_cpu_tdp_watts(),
    }


def detect_cpu_model() -> str:
    output = run_text(["lscpu"])
    for line in output.splitlines():
        if line.lower().startswith("model name:"):
            return line.split(":", 1)[1].strip()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def read_memory_total_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kib = float(line.split()[1])
                return round(kib / 1024 / 1024, 2)
    except (OSError, ValueError, IndexError):
        return 0.0
    return 0.0


def first_ip_address() -> str:
    output = run_text(["hostname", "-I"])
    for item in output.split():
        if item and not item.startswith("127."):
            return item
    return ""


def detect_cpu_tdp_watts() -> float:
    return SHAHEEN_CPU_TDP_WATTS


def run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def parse_iso_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except ValueError:
        return None


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    session_dir = Path(os.environ["BURNER_SLURM_SESSION_DIR"])
    repo_root = Path(os.environ.get("BURNER_REPO_ROOT", Path.cwd()))
    poll_ms = int(os.environ.get("BURNER_WORKER_POLL_MS", str(DEFAULT_POLL_MS)))
    worker = Worker(session_dir=session_dir, repo_root=repo_root, poll_ms=poll_ms)
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
