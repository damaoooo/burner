from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


UI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_ROOT.parent
MACHINES_PATH = UI_ROOT / "machines.json"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MachineConfig:
    id: str
    name: str
    host: str
    port: int
    username: str
    identity_file: str
    workdir: str
    cpu_tdp: float
    gpu_tdp: float
    conda_env: str

    @property
    def expanded_identity_file(self) -> str:
        return str(Path(self.identity_file).expanduser())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "identity_file": self.identity_file,
            "workdir": self.workdir,
            "cpu_tdp": self.cpu_tdp,
            "gpu_tdp": self.gpu_tdp,
            "conda_env": self.conda_env,
        }


class ConfigStore:
    def __init__(self, path: Path = MACHINES_PATH):
        self.path = path
        self._machines: dict[str, MachineConfig] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self._machines = {}
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid machines.json: {exc}") from exc

        machines = raw.get("machines")
        if not isinstance(machines, list):
            raise ConfigError("machines.json must contain a 'machines' list")

        parsed: dict[str, MachineConfig] = {}
        for index, item in enumerate(machines):
            machine = _parse_machine(item, index)
            if machine.id in parsed:
                raise ConfigError(f"duplicate machine id: {machine.id}")
            parsed[machine.id] = machine
        self._machines = parsed

    def list_machines(self) -> list[MachineConfig]:
        self.reload()
        return list(self._machines.values())

    def get_machine(self, machine_id: str) -> MachineConfig:
        self.reload()
        try:
            return self._machines[machine_id]
        except KeyError as exc:
            raise ConfigError(f"unknown machine id: {machine_id}") from exc


def _parse_machine(item: Any, index: int) -> MachineConfig:
    if not isinstance(item, dict):
        raise ConfigError(f"machine #{index + 1} must be an object")

    required = [
        "id",
        "name",
        "host",
        "username",
        "identity_file",
        "workdir",
        "cpu_tdp",
        "gpu_tdp",
        "conda_env",
    ]
    missing = [key for key in required if key not in item]
    if missing:
        raise ConfigError(f"machine #{index + 1} missing fields: {', '.join(missing)}")

    machine_id = _require_string(item, "id", index)
    return MachineConfig(
        id=machine_id,
        name=_require_string(item, "name", index),
        host=_require_string(item, "host", index),
        port=int(item.get("port", 22)),
        username=_require_string(item, "username", index),
        identity_file=_require_string(item, "identity_file", index),
        workdir=_require_string(item, "workdir", index),
        cpu_tdp=float(item["cpu_tdp"]),
        gpu_tdp=float(item["gpu_tdp"]),
        conda_env=_require_string(item, "conda_env", index),
    )


def _require_string(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"machine #{index + 1} field '{key}' must be a non-empty string")
    return value.strip()
