from __future__ import annotations

import csv
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
DEFAULT_POLL_MS = 10
HEARTBEAT_SECONDS = 1.0
DEFAULT_SAMPLE_MS = 200
BURN_SAMPLE_SECONDS = 0.05
CPU_FREQ_SAMPLE_LIMIT = 8
SHAHEEN_CPU_TDP_WATTS = 360.0


class NullGpuSampler:
    status = "disabled on Shaheen"

    def sample(self):
        return None


class Worker:
    def __init__(
        self,
        session_dir: Path,
        repo_root: Path,
        poll_ms: int,
        sample_ms: int,
        local_sample_dir: Path | None = None,
    ):
        self.session_dir = session_dir
        self.repo_root = repo_root
        self.poll_seconds = max(0.001, poll_ms / 1000.0)
        self.sample_seconds = max(0.03, sample_ms / 1000.0)
        self.node_id = os.environ.get("SLURMD_NODENAME") or socket.gethostname()
        self.hostname = socket.gethostname()
        self.node_path = self.session_dir / "nodes" / f"{self.node_id}.json"
        self.sample_path = self.session_dir / "samples" / f"{self.node_id}.csv"
        local_dir = Path(
            local_sample_dir
            or os.environ.get("BURNER_WORKER_LOCAL_SAMPLE_DIR", "/tmp")
        )
        self.local_sample_path = local_dir / f"burner-{self.session_dir.name}-{self.node_id}.csv"
        self.log_dir = self.session_dir / "logs"
        self.status = "initializing"
        self.message = ""
        self.hw_info = collect_hw_info()
        self.latest_power: dict[str, object] | None = None
        self._previous_cpu_times = read_cpu_times()
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

        next_sample = 0.0
        next_state = 0.0
        while not self._stop:
            now = time.monotonic()
            self._handle_command()
            self._poll_burner()
            if now >= next_sample:
                burn_active = self._burn_is_active()
                self._sample_power(write_local=burn_active)
                next_sample = now + (BURN_SAMPLE_SECONDS if self.burner is not None else self.sample_seconds)
            if now >= next_state:
                self._write_state()
                next_state = now + HEARTBEAT_SECONDS
            time.sleep(self.poll_seconds)

        self._stop_burner()
        self._finalize_samples()
        self.status = "stopped"
        self._write_state()
        return 0

    def _prepare_dirs(self) -> None:
        for relative in ("nodes", "samples", "logs"):
            (self.session_dir / relative).mkdir(parents=True, exist_ok=True)

    def _write_sample_header(self, path: Path | None = None) -> None:
        path = path or self.local_sample_path
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp",
                    "cpu_watts",
                    "cpu_watts_estimated",
                    "cpu_utilization_percent",
                    "cpu_freq_mhz_avg",
                    "cpu_freq_mhz_min",
                    "cpu_freq_mhz_max",
                    "loadavg_1m",
                ]
            )

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
            self._finalize_samples()
            self.status = "ready"
            self.message = ""
        elif action == "release":
            self._stop_burner()
            self._finalize_samples()
            self._stop = True
        self.last_sequence = sequence
        self._write_state()

    def _start_burner(self, command: dict[str, Any], sequence: int) -> None:
        self._stop_burner()
        self._reset_samples()
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
        self._finalize_samples()

    def _burn_is_active(self) -> bool:
        return (
            self.burner is not None
            and self.burner.poll() is None
            and self.burner_start_at is not None
            and time.time() >= self.burner_start_at
        )

    def _sample_power(self, write_local: bool) -> None:
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
        metrics = collect_runtime_metrics(self._previous_cpu_times, self.hw_info)
        self._previous_cpu_times = metrics.pop("_cpu_times", self._previous_cpu_times)
        cpu_watts_estimated = metrics.get("cpu_watts_estimated")
        cpu_watts_display = cpu_watts if cpu_watts is not None else cpu_watts_estimated
        watts_source = "rapl" if cpu_watts is not None else "estimated" if cpu_watts_estimated is not None else "unavailable"
        self.latest_power = {
            "timestamp": timestamp,
            "cpu_watts": cpu_watts,
            "cpu_watts_estimated": cpu_watts_estimated,
            "cpu_watts_display": cpu_watts_display,
            "cpu_watts_source": watts_source,
            "status": runtime_status(self._sampler.status, watts_source),
            **metrics,
        }
        if not write_local:
            return
        self._write_sample_header(self.local_sample_path)
        with self.local_sample_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    timestamp,
                    format_optional_float(cpu_watts),
                    format_optional_float(cpu_watts_estimated),
                    format_optional_float(metrics.get("cpu_utilization_percent")),
                    format_optional_float(metrics.get("cpu_freq_mhz_avg")),
                    format_optional_float(metrics.get("cpu_freq_mhz_min")),
                    format_optional_float(metrics.get("cpu_freq_mhz_max")),
                    format_optional_float(metrics.get("loadavg_1m")),
                ]
            )

    def _reset_samples(self) -> None:
        self._previous_cpu_times = read_cpu_times()
        for path in (self.local_sample_path, self.sample_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._write_sample_header(self.local_sample_path)

    def _finalize_samples(self) -> None:
        if not self.local_sample_path.exists():
            return
        self.sample_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.sample_path.with_name(f".{self.sample_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(self.local_sample_path, tmp_path)
            tmp_path.replace(self.sample_path)
            self.local_sample_path.unlink()
        except OSError as exc:
            with suppress(OSError):
                tmp_path.unlink()
            self.message = f"failed to move local samples to shared storage: {exc}"

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
    socket_count = detect_cpu_socket_count()
    return {
        "cpu_model": detect_cpu_model(),
        "cpu_count": os.cpu_count() or 0,
        "cpu_socket_count": socket_count,
        "memory_total_gb": read_memory_total_gb(),
        "ip_address": first_ip_address(),
        "cpu_tdp_watts": detect_cpu_tdp_watts(),
        "cpu_tdp_per_socket_watts": SHAHEEN_CPU_TDP_WATTS,
        "cpu_tdp_total_watts": round(socket_count * SHAHEEN_CPU_TDP_WATTS, 2),
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


def detect_cpu_socket_count() -> int:
    output = run_text(["lscpu"])
    for line in output.splitlines():
        if line.lower().startswith("socket(s):"):
            try:
                return max(1, int(line.split(":", 1)[1].strip()))
            except ValueError:
                break

    packages = set()
    for path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/topology/physical_package_id"):
        try:
            packages.add(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
    return max(1, len(packages))


def collect_runtime_metrics(
    previous_cpu_times: tuple[int, int] | None,
    hw_info: dict[str, object],
) -> dict[str, object]:
    current_cpu_times = read_cpu_times()
    utilization = cpu_utilization_percent(previous_cpu_times, current_cpu_times)
    frequencies = read_cpu_frequency_summary()
    loadavg_1m = read_loadavg_1m()
    cpu_count = int(hw_info.get("cpu_count") or os.cpu_count() or 0)
    total_tdp = float(hw_info.get("cpu_tdp_total_watts") or hw_info.get("cpu_tdp_watts") or 0.0)
    estimated_watts = estimate_cpu_watts(utilization, total_tdp)
    load_per_cpu = None
    if loadavg_1m is not None and cpu_count > 0:
        load_per_cpu = round((loadavg_1m / cpu_count) * 100.0, 2)

    return {
        "_cpu_times": current_cpu_times,
        "cpu_utilization_percent": utilization,
        "cpu_watts_estimated": estimated_watts,
        "cpu_tdp_total_watts": total_tdp,
        "loadavg_1m": loadavg_1m,
        "loadavg_per_cpu_percent": load_per_cpu,
        **frequencies,
    }


def read_cpu_frequency_summary(
    root: Path = Path("/sys/devices/system/cpu/cpufreq"),
    limit: int = CPU_FREQ_SAMPLE_LIMIT,
) -> dict[str, object]:
    values = []
    for path in sorted(root.glob("policy*/scaling_cur_freq"))[:limit]:
        try:
            khz = float(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if khz > 0:
            values.append(khz / 1000.0)

    if not values:
        values = read_cpuinfo_mhz_values()

    if not values:
        return {
            "cpu_freq_mhz_avg": None,
            "cpu_freq_mhz_min": None,
            "cpu_freq_mhz_max": None,
            "cpu_freq_sample_count": 0,
        }

    return {
        "cpu_freq_mhz_avg": round(sum(values) / len(values), 2),
        "cpu_freq_mhz_min": round(min(values), 2),
        "cpu_freq_mhz_max": round(max(values), 2),
        "cpu_freq_sample_count": len(values),
    }


def read_cpuinfo_mhz_values(path: Path = Path("/proc/cpuinfo")) -> list[float]:
    values = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line.lower().startswith("cpu mhz"):
            continue
        try:
            mhz = float(line.split(":", 1)[1].strip())
        except (IndexError, ValueError):
            continue
        if mhz > 0:
            values.append(mhz)
    return values


def read_cpu_times(path: Path = Path("/proc/stat")) -> tuple[int, int] | None:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first_line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        values = [int(value) for value in parts[1:]]
    except ValueError:
        return None
    if len(values) < 5:
        return None
    idle = values[3] + values[4]
    total = sum(values)
    return idle, total


def cpu_utilization_percent(
    previous: tuple[int, int] | None,
    current: tuple[int, int] | None,
) -> float | None:
    if previous is None or current is None:
        return None
    previous_idle, previous_total = previous
    current_idle, current_total = current
    total_delta = current_total - previous_total
    idle_delta = current_idle - previous_idle
    if total_delta <= 0 or idle_delta < 0:
        return None
    busy = max(0, total_delta - idle_delta)
    return round(min(100.0, (busy / total_delta) * 100.0), 2)


def estimate_cpu_watts(utilization_percent: float | None, cpu_tdp_total_watts: float) -> float | None:
    if utilization_percent is None or cpu_tdp_total_watts <= 0:
        return None
    return round(cpu_tdp_total_watts * (utilization_percent / 100.0), 2)


def read_loadavg_1m() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def runtime_status(base_status: str, watts_source: str) -> str:
    if watts_source == "rapl":
        return base_status
    if watts_source == "estimated":
        return f"{base_status}; CPU watts estimated from utilization and Shaheen TDP"
    return f"{base_status}; CPU watts unavailable"


def format_optional_float(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.6f}"
    return ""


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
    sample_ms = int(os.environ.get("BURNER_WORKER_SAMPLE_MS", str(DEFAULT_SAMPLE_MS)))
    worker = Worker(session_dir=session_dir, repo_root=repo_root, poll_ms=poll_ms, sample_ms=sample_ms)
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
