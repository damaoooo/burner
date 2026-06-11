from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import psutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / ".runtime"
DEFAULT_PID_FILE = DEFAULT_RUNTIME_DIR / "choker.pid"
DEFAULT_LOG_FILE = DEFAULT_RUNTIME_DIR / "choker.log"


class PidStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True)
class PidState:
    state: PidStatus
    path: Path
    pid: int | None = None
    message: str = ""


class PidFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.pid = os.getpid()
        self.acquired = False

    def acquire(self) -> None:
        status = read_pid_status(self.path, require_choker=True)
        if status.state == PidStatus.RUNNING:
            raise RuntimeError(f"choker is already running with pid {status.pid}")
        if status.state in {PidStatus.STALE, PidStatus.INVALID}:
            remove_pid_file(self.path)
        write_pid_file(self.path, self.pid)
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        current = _read_pid(self.path)
        if current == self.pid:
            remove_pid_file(self.path)
        self.acquired = False

    def __enter__(self) -> "PidFile":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.release()


def write_pid_file(path: str | Path, pid: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid}) + "\n", encoding="utf-8")


def remove_pid_file(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return


def read_pid_status(path: str | Path, require_choker: bool = False) -> PidState:
    path = Path(path)
    if not path.exists():
        return PidState(state=PidStatus.STOPPED, path=path)

    pid = _read_pid(path)
    if pid is None:
        return PidState(
            state=PidStatus.INVALID,
            path=path,
            message="pidfile is not valid JSON or an integer",
        )
    if not is_pid_alive(pid):
        return PidState(state=PidStatus.STALE, path=path, pid=pid)
    if require_choker and not is_choker_process(pid):
        return PidState(
            state=PidStatus.STALE,
            path=path,
            pid=pid,
            message="pid belongs to a non-choker process",
        )
    return PidState(state=PidStatus.RUNNING, path=path, pid=pid)


def terminate_process(pid: int, timeout: float) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.05)
    return not is_pid_alive(pid)


def is_pid_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        return True
    except (psutil.Error, ProcessLookupError):
        return False


def is_choker_process(pid: int) -> bool:
    try:
        command = " ".join(psutil.Process(pid).cmdline())
    except (psutil.Error, ProcessLookupError):
        return False
    return "choker" in command


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
        pid = data["pid"] if isinstance(data, dict) else data
    except (json.JSONDecodeError, KeyError, TypeError):
        try:
            pid = int(text)
        except ValueError:
            return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None
