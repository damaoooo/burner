from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from config import REPO_ROOT
from remote_shell import conda_run_command


MIN_SAMPLING_MS = 10
MAX_SAMPLING_MS = 1000
DEFAULT_SAMPLING_MS = 100

SOURCE_FILES: tuple[str, ...] = (
    "scripts/build_lookbusy.sh",
    "scripts/build_gpu_burn.sh",
    "third_party/lookbusy/lb.c",
    "third_party/gpu-burn/gpu_burn-drv.cpp",
    "warpper/burner_cli.py",
)

Broadcast = Callable[[dict[str, object]], Awaitable[None]]


class SamplingError(RuntimeError):
    pass


class SamplingConflictError(SamplingError):
    pass


class ConfigLike(Protocol):
    def get_machine(self, machine_id: str): ...


class SSHLike(Protocol):
    def status_for(self, machine_id: str) -> str: ...
    def get_connection(self, machine_id: str): ...
    async def run_command(self, machine_id: str, cmd: str) -> tuple[str, str, int]: ...


class FileTransferLike(Protocol):
    async def scp_to_remote(
        self,
        machine_id: str,
        local_path: str | Path,
        remote_path: str,
    ) -> None: ...


class BurnLike(Protocol):
    def has_jobs(self, machine_id: str) -> bool: ...


@dataclass
class SamplingMachineStatus:
    machine_id: str
    sampling_ms: int
    status: str = "queued"
    step: str = "queued"
    progress: float = 0.0
    exit_code: int | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.machine_id,
            "sampling_ms": self.sampling_ms,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
        }
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.message:
            payload["message"] = self.message
        return payload


def validate_sampling_ms(value: int) -> int:
    if not isinstance(value, int):
        raise SamplingError("sampling_ms must be an integer")
    if value < MIN_SAMPLING_MS or value > MAX_SAMPLING_MS:
        raise SamplingError(
            f"sampling_ms must be between {MIN_SAMPLING_MS} and {MAX_SAMPLING_MS} ms"
        )
    return value


class SamplingController:
    def __init__(
        self,
        config: ConfigLike,
        ssh: SSHLike,
        file_transfer: FileTransferLike,
        burn: BurnLike,
        broadcast: Broadcast,
    ):
        self._config = config
        self._ssh = ssh
        self._file_transfer = file_transfer
        self._burn = burn
        self._broadcast = broadcast
        self._running = False
        self._status: dict[str, SamplingMachineStatus] = {}
        self._lock = asyncio.Lock()

    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "machines": [status.to_dict() for status in self._status.values()],
        }

    async def reserve_apply(self, sampling_ms: int, machine_ids: list[str]) -> None:
        sampling_ms = validate_sampling_ms(sampling_ms)
        if not machine_ids:
            raise SamplingError("at least one connected machine is required")

        async with self._lock:
            if self._running:
                raise SamplingConflictError("Sampling rebuild is already running")
            seen: set[str] = set()
            for machine_id in machine_ids:
                if machine_id in seen:
                    raise SamplingError(f"duplicate machine id: {machine_id}")
                seen.add(machine_id)
                self._config.get_machine(machine_id)
                if self._ssh.status_for(machine_id) != "connected":
                    raise SamplingError(f"machine {machine_id} is not connected")
                if self._burn.has_jobs(machine_id):
                    raise SamplingConflictError(f"machine {machine_id} is currently burning")

            self._running = True
            self._status = {
                machine_id: SamplingMachineStatus(machine_id, sampling_ms)
                for machine_id in machine_ids
            }

    async def run_reserved_apply(
        self,
        sampling_ms: int,
        machine_ids: list[str],
        has_gpu_by_machine: dict[str, bool],
    ) -> None:
        exit_code = 0
        try:
            results = await asyncio.gather(
                *(
                    self._run_machine(
                        machine_id,
                        sampling_ms,
                        has_gpu=has_gpu_by_machine.get(machine_id, False),
                    )
                    for machine_id in machine_ids
                ),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception) or result != 0:
                    exit_code = 1
        finally:
            self._running = False
            await self._broadcast(
                {
                    "event": "sampling_build_complete",
                    "sampling_ms": sampling_ms,
                    "exit_code": exit_code,
                }
            )

    async def _run_machine(
        self,
        machine_id: str,
        sampling_ms: int,
        has_gpu: bool,
    ) -> int:
        steps = ["reset", "pull", "submodules", "sync", "build_cpu"]
        if has_gpu:
            steps.append("build_gpu")
        total = len(steps)

        try:
            await self._run_remote_step(
                machine_id,
                sampling_ms,
                step="reset",
                command=reset_command(),
                completed=0,
                total=total,
            )
            await self._run_remote_step(
                machine_id,
                sampling_ms,
                step="pull",
                command="git pull --recurse-submodules",
                completed=1,
                total=total,
            )
            await self._run_remote_step(
                machine_id,
                sampling_ms,
                step="submodules",
                command="git submodule sync --recursive && git submodule update --init --recursive --force",
                completed=2,
                total=total,
            )
            await self._sync_sources(machine_id, sampling_ms, completed=3, total=total)
            await self._run_remote_step(
                machine_id,
                sampling_ms,
                step="build_cpu",
                command=build_command(sampling_ms, "bash scripts/build_lookbusy.sh"),
                completed=4,
                total=total,
            )
            if has_gpu:
                await self._run_remote_step(
                    machine_id,
                    sampling_ms,
                    step="build_gpu",
                    command=build_command(sampling_ms, "bash scripts/build_gpu_burn.sh"),
                    completed=5,
                    total=total,
                )
            await self._finish_machine(machine_id, sampling_ms, exit_code=0)
            return 0
        except Exception as exc:
            await self._log(machine_id, f"sampling rebuild failed: {exc}")
            await self._finish_machine(machine_id, sampling_ms, exit_code=1, message=str(exc))
            return 1

    async def _run_remote_step(
        self,
        machine_id: str,
        sampling_ms: int,
        step: str,
        command: str,
        completed: int,
        total: int,
    ) -> None:
        machine = self._config.get_machine(machine_id)
        await self._progress(machine_id, sampling_ms, step, completed, total, "running")
        await self._log(machine_id, f"[{step}] {command}")

        inner = f"cd {shlex.quote(machine.workdir)} && {command}"
        full_cmd = conda_run_command(machine.conda_env, inner)
        conn = self._ssh.get_connection(machine_id)
        async with conn.create_process(full_cmd) as process:
            await asyncio.gather(
                self._stream_lines(machine_id, process.stdout),
                self._stream_lines(machine_id, process.stderr),
            )
            exit_status = process.exit_status
        if exit_status != 0:
            raise SamplingError(f"{step} failed with exit code {exit_status}")
        await self._progress(machine_id, sampling_ms, step, completed + 1, total, "running")

    async def _sync_sources(
        self,
        machine_id: str,
        sampling_ms: int,
        completed: int,
        total: int,
    ) -> None:
        machine = self._config.get_machine(machine_id)
        await self._progress(machine_id, sampling_ms, "sync", completed, total, "running")
        remote_dirs = sorted({str(Path(machine.workdir) / Path(path).parent) for path in SOURCE_FILES})
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(path) for path in remote_dirs)
        stdout, stderr, exit_code = await self._ssh.run_command(machine_id, mkdir_cmd)
        if stdout.strip():
            await self._log(machine_id, stdout.strip())
        if stderr.strip():
            await self._log(machine_id, stderr.strip())
        if exit_code != 0:
            raise SamplingError(f"sync mkdir failed with exit code {exit_code}")

        for relative in SOURCE_FILES:
            local_path = REPO_ROOT / relative
            remote_path = str(Path(machine.workdir) / relative)
            await self._log(machine_id, f"[sync] scp {relative}")
            await self._file_transfer.scp_to_remote(machine_id, local_path, remote_path)
        await self._progress(machine_id, sampling_ms, "sync", completed + 1, total, "running")

    async def _stream_lines(self, machine_id: str, stream) -> None:
        async for line in stream:
            await self._log(machine_id, line.rstrip())

    async def _log(self, machine_id: str, line: str) -> None:
        if not line:
            return
        await self._broadcast(
            {"event": "sampling_build_log", "id": machine_id, "line": line}
        )

    async def _progress(
        self,
        machine_id: str,
        sampling_ms: int,
        step: str,
        completed: int,
        total: int,
        status: str,
    ) -> None:
        progress = max(0.0, min(1.0, completed / max(1, total)))
        machine_status = self._status.get(machine_id)
        if machine_status is not None:
            machine_status.status = status
            machine_status.step = step
            machine_status.progress = progress
        await self._broadcast(
            {
                "event": "sampling_build_progress",
                "id": machine_id,
                "sampling_ms": sampling_ms,
                "step": step,
                "status": status,
                "completed": completed,
                "total": total,
                "progress": progress,
            }
        )

    async def _finish_machine(
        self,
        machine_id: str,
        sampling_ms: int,
        exit_code: int,
        message: str | None = None,
    ) -> None:
        status = "success" if exit_code == 0 else "failed"
        machine_status = self._status.get(machine_id)
        if machine_status is not None:
            machine_status.status = status
            machine_status.step = status
            machine_status.progress = 1.0
            machine_status.exit_code = exit_code
            machine_status.message = message
        payload: dict[str, object] = {
            "event": "sampling_build_done",
            "id": machine_id,
            "sampling_ms": sampling_ms,
            "exit_code": exit_code,
            "status": status,
        }
        if message:
            payload["message"] = message
        await self._broadcast(payload)


def build_command(sampling_ms: int, command: str) -> str:
    sampling_ms = validate_sampling_ms(sampling_ms)
    return f"BURNER_CONTROL_INTERVAL_MS={sampling_ms} {command}"


def reset_command() -> str:
    return (
        "git reset --hard HEAD && "
        "git clean -fd && "
        "git submodule foreach --recursive 'git reset --hard HEAD && git clean -fd'"
    )
