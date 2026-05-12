from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import asyncssh

from config import ConfigStore


ConnectionStatus = Literal["disconnected", "connecting", "connected", "error"]
StatusCallback = Callable[[str, ConnectionStatus, str | None], Awaitable[None]]


class SSHManager:
    def __init__(self, config: ConfigStore):
        self._config = config
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}
        self._statuses: dict[str, ConnectionStatus] = {}
        self._errors: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._status_callback: StatusCallback | None = None

    def set_status_callback(self, callback: StatusCallback) -> None:
        self._status_callback = callback

    def status_for(self, machine_id: str) -> ConnectionStatus:
        return self._statuses.get(machine_id, "disconnected")

    def error_for(self, machine_id: str) -> str | None:
        return self._errors.get(machine_id)

    def get_connection(self, machine_id: str) -> asyncssh.SSHClientConnection:
        conn = self._connections.get(machine_id)
        if conn is None or self.status_for(machine_id) != "connected":
            raise RuntimeError(f"machine {machine_id} is not connected")
        return conn

    async def connect(self, machine_id: str) -> None:
        machine = self._config.get_machine(machine_id)
        lock = self._locks.setdefault(machine_id, asyncio.Lock())

        async with lock:
            if self.status_for(machine_id) == "connected":
                return
            await self._set_status(machine_id, "connecting")
            try:
                conn = await asyncssh.connect(
                    machine.host,
                    port=machine.port,
                    username=machine.username,
                    client_keys=[machine.expanded_identity_file],
                    known_hosts=None,
                    keepalive_interval=30,
                    keepalive_count_max=3,
                )
            except Exception as exc:
                self._connections.pop(machine_id, None)
                await self._set_status(machine_id, "error", str(exc))
                raise

            old_conn = self._connections.get(machine_id)
            if old_conn is not None:
                old_conn.close()
            self._connections[machine_id] = conn
            await self._set_status(machine_id, "connected")

    async def disconnect(self, machine_id: str) -> None:
        conn = self._connections.pop(machine_id, None)
        if conn is not None:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass
        await self._set_status(machine_id, "disconnected")

    async def run_command(self, machine_id: str, cmd: str) -> tuple[str, str, int]:
        conn = self.get_connection(machine_id)
        result = await conn.run(cmd, check=False)
        return result.stdout, result.stderr, result.exit_status

    async def _set_status(
        self,
        machine_id: str,
        status: ConnectionStatus,
        message: str | None = None,
    ) -> None:
        self._statuses[machine_id] = status
        if message:
            self._errors[machine_id] = message
        elif status != "error":
            self._errors.pop(machine_id, None)

        if self._status_callback is not None:
            await self._status_callback(machine_id, status, message)
