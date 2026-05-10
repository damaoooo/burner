from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BackendError(RuntimeError):
    """Raised when a burn backend cannot start or update."""


@dataclass(frozen=True)
class BurnEvent:
    backend: str
    elapsed: float
    intensity: float


class BurnBackend:
    name = "backend"

    def set_intensity(self, intensity: float, elapsed: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class MockBurnBackend(BurnBackend):
    def __init__(self, name: str):
        self.name = name
        self.events: list[BurnEvent] = []
        self.stopped = False

    def set_intensity(self, intensity: float, elapsed: float) -> None:
        self.events.append(BurnEvent(self.name, elapsed, intensity))

    def stop(self) -> None:
        self.stopped = True


class LookbusyCpuBackend(BurnBackend):
    name = "cpu"

    def __init__(
        self,
        binary: Path | None = None,
        ncpus: int | None = None,
    ):
        self.binary = binary or PROJECT_ROOT / "third_party" / "lookbusy" / "lookbusy"
        self.ncpus = ncpus
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._control_file: Path | None = None
        self._process: subprocess.Popen[bytes] | None = None

    def set_intensity(self, intensity: float, elapsed: float) -> None:
        del elapsed
        self._ensure_started(intensity)
        assert self._control_file is not None
        self._control_file.write_text(f"{intensity * 100.0:.6f}\n", encoding="utf-8")

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            _terminate_process_group(self._process, timeout=2)
        self._process = None
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        self._tmpdir = None
        self._control_file = None

    def _ensure_started(self, initial_intensity: float) -> None:
        if not self.binary.exists():
            raise BackendError(
                f"lookbusy binary not found at {self.binary}; run scripts/build_lookbusy.sh"
            )
        if self._process is not None:
            if self._process.poll() is not None:
                raise BackendError("lookbusy exited unexpectedly")
            return

        self._tmpdir = tempfile.TemporaryDirectory(prefix="burner-lookbusy-")
        self._control_file = Path(self._tmpdir.name) / "cpu_util_percent"
        self._control_file.write_text(
            f"{initial_intensity * 100.0:.6f}\n",
            encoding="utf-8",
        )
        command = [
            str(self.binary),
            "-q",
            "-c",
            "1",
            "--cpu-util-file",
            str(self._control_file),
        ]
        if self.ncpus is not None:
            command.extend(["-n", str(self.ncpus)])
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )


class DutyCycleGpuBackend(BurnBackend):
    name = "gpu"

    def __init__(
        self,
        binary: Path | None = None,
        memory_mb: int = 900,
    ):
        self.binary = binary or PROJECT_ROOT / "third_party" / "gpu-burn" / "gpu_burn"
        self.memory_mb = memory_mb
        self._process: subprocess.Popen[bytes] | None = None
        self._log_file = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._control_file: Path | None = None

    def set_intensity(self, intensity: float, elapsed: float) -> None:
        del elapsed
        self._ensure_started(intensity)
        assert self._control_file is not None
        self._control_file.write_text(f"{intensity * 100.0:.6f}\n", encoding="utf-8")

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            _terminate_process_group(self._process)
        self._process = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        self._tmpdir = None
        self._control_file = None

    def _ensure_started(self, initial_intensity: float) -> None:
        if not self.binary.exists():
            raise BackendError(
                f"gpu_burn binary not found at {self.binary}; run scripts/build_gpu_burn.sh"
            )
        if self._process is not None:
            if self._process.poll() is not None:
                raise BackendError(
                    "gpu_burn exited unexpectedly"
                    + _format_backend_log(self._log_file)
                )
            return

        self._tmpdir = tempfile.TemporaryDirectory(prefix="burner-gpu-burn-")
        self._control_file = Path(self._tmpdir.name) / "gpu_util_percent"
        self._control_file.write_text(
            f"{initial_intensity * 100.0:.6f}\n",
            encoding="utf-8",
        )
        self._log_file = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            [
                str(self.binary),
                "-m",
                str(self.memory_mb),
                "-stts",
                "1",
                "--burn-util-file",
                str(self._control_file),
                "86400",
            ],
            cwd=str(self.binary.parent),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )


def _terminate_process_group(process: subprocess.Popen[bytes], timeout: float = 5) -> None:
    try:
        process_group = os.getpgid(process.pid)
        os.killpg(process_group, signal.SIGCONT)
        os.killpg(process_group, signal.SIGTERM)
        process.wait(timeout=timeout)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)


def _format_backend_log(log_file) -> str:
    if log_file is None:
        return ""
    try:
        log_file.flush()
        log_file.seek(0)
        text = log_file.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    lines = text.splitlines()[-12:]
    return ":\n" + "\n".join(lines)
