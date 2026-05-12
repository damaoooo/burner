from __future__ import annotations

from pathlib import Path

import asyncssh

from ssh_manager import SSHManager


class FileTransfer:
    def __init__(self, ssh: SSHManager):
        self._ssh = ssh

    async def scp_to_remote(
        self,
        machine_id: str,
        local_path: str | Path,
        remote_path: str,
    ) -> None:
        conn = self._ssh.get_connection(machine_id)
        await asyncssh.scp(str(local_path), (conn, remote_path))
