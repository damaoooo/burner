from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from typing import Protocol

from remote_shell import conda_run_command
from sampling_controller import reset_command


Broadcast = Callable[[dict[str, object]], Awaitable[None]]


class ConfigLike(Protocol):
    def get_machine(self, machine_id: str): ...


class SSHLike(Protocol):
    def get_connection(self, machine_id: str): ...


class BurnLike(Protocol):
    def has_jobs(self, machine_id: str) -> bool: ...


class UpdateConflictError(RuntimeError):
    pass


class UpdateController:
    def __init__(
        self,
        config: ConfigLike,
        ssh: SSHLike,
        burn: BurnLike,
        broadcast: Broadcast,
    ):
        self._config = config
        self._ssh = ssh
        self._burn = burn
        self._broadcast = broadcast
        self._running: set[str] = set()

    async def run_update(self, machine_id: str, has_gpu: bool) -> None:
        if self._burn.has_jobs(machine_id):
            raise UpdateConflictError("Machine is currently burning")
        if machine_id in self._running:
            raise UpdateConflictError("Update is already running")

        self._running.add(machine_id)
        try:
            await self._run_update_inner(machine_id, has_gpu)
        finally:
            self._running.discard(machine_id)

    async def _run_update_inner(self, machine_id: str, has_gpu: bool) -> None:
        machine = self._config.get_machine(machine_id)
        commands = [
            f"cd {shlex.quote(machine.workdir)}",
            reset_command(),
            "git pull --recurse-submodules",
            "git submodule sync --recursive",
            "git submodule update --init --recursive --force",
            "bash scripts/build_lookbusy.sh",
        ]
        if has_gpu:
            commands.append("bash scripts/build_gpu_burn.sh")

        inner = " && ".join(commands)
        full_cmd = conda_run_command(machine.conda_env, inner)
        conn = self._ssh.get_connection(machine_id)
        exit_code = 1
        try:
            async with conn.create_process(full_cmd) as process:
                await asyncio.gather(
                    self._stream_lines(machine_id, process.stdout),
                    self._stream_lines(machine_id, process.stderr),
                )
                exit_code = process.exit_status
        finally:
            await self._broadcast(
                {"event": "update_done", "id": machine_id, "exit_code": exit_code}
            )

    async def _stream_lines(self, machine_id: str, stream) -> None:
        async for line in stream:
            await self._broadcast(
                {"event": "update_log", "id": machine_id, "line": line.rstrip()}
            )

    def is_running(self, machine_id: str | None = None) -> bool:
        if machine_id is None:
            return bool(self._running)
        return machine_id in self._running
