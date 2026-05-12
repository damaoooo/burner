from __future__ import annotations

import re
from typing import Any

from config import ConfigStore
from ssh_manager import SSHManager


async def query_hw_info(
    machine_id: str,
    config: ConfigStore,
    ssh: SSHManager,
) -> dict[str, Any]:
    machine = config.get_machine(machine_id)

    cpu_stdout, _, cpu_exit = await ssh.run_command(
        machine_id,
        r"""lscpu | grep "Model name" | awk -F: '{print $2}' | xargs""",
    )
    cpu_model = cpu_stdout.strip() if cpu_exit == 0 else ""

    gpu_stdout, _, gpu_exit = await ssh.run_command(
        machine_id,
        "command -v nvidia-smi >/dev/null 2>&1 && "
        "nvidia-smi --query-gpu=name,power.max_limit --format=csv,noheader",
    )
    gpus = _parse_gpu_rows(gpu_stdout, machine.gpu_tdp) if gpu_exit == 0 else []

    return {
        "cpu_model": cpu_model,
        "cpu_tdp": machine.cpu_tdp,
        "gpu_tdp": machine.gpu_tdp,
        "gpus": gpus,
    }


def _parse_gpu_rows(text: str, fallback_tdp: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw_line in enumerate(line.strip() for line in text.splitlines()):
        if not raw_line:
            continue
        parts = [part.strip() for part in raw_line.split(",", 1)]
        name = parts[0]
        tdp = 0.0
        if len(parts) == 2:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", parts[1])
            if match:
                tdp = float(match.group(1))
        if tdp <= 0:
            tdp = fallback_tdp
        rows.append({"index": index, "name": name, "tdp_watts": tdp})
    return rows
