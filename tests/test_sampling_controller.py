import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from sampling_controller import (  # noqa: E402
    SOURCE_FILES,
    SamplingController,
    SamplingError,
    build_command,
    validate_sampling_ms,
)


@pytest.mark.parametrize("value", [10, 100, 1000])
def test_validate_sampling_ms_accepts_bounds(value):
    assert validate_sampling_ms(value) == value


@pytest.mark.parametrize("value", [9, 1001])
def test_validate_sampling_ms_rejects_out_of_range(value):
    with pytest.raises(SamplingError):
        validate_sampling_ms(value)


def test_build_command_injects_sampling_env():
    assert (
        build_command(250, "bash scripts/build_lookbusy.sh")
        == "BURNER_CONTROL_INTERVAL_MS=250 bash scripts/build_lookbusy.sh"
    )


def test_sampling_controller_runs_reset_pull_scp_then_builds():
    async def run_test():
        config = FakeConfig()
        ssh = FakeSSH()
        transfer = FakeTransfer()
        events = []

        async def broadcast(payload):
            events.append(payload)

        controller = SamplingController(config, ssh, transfer, FakeBurn(), broadcast)
        await controller.reserve_apply(250, ["node-1"])
        await controller.run_reserved_apply(250, ["node-1"], {"node-1": True})

        remote_commands = "\n".join(ssh.process_commands)
        assert "git reset --hard HEAD" in ssh.process_commands[0]
        assert "git pull --recurse-submodules" in ssh.process_commands[1]
        assert "BURNER_CONTROL_INTERVAL_MS=250 bash scripts/build_lookbusy.sh" in remote_commands
        assert "BURNER_CONTROL_INTERVAL_MS=250 bash scripts/build_gpu_burn.sh" in remote_commands
        assert {item[1] for item in transfer.copies} == {
            f"/remote/burner/{relative}" for relative in SOURCE_FILES
        }
        assert events[-1] == {
            "event": "sampling_build_complete",
            "sampling_ms": 250,
            "exit_code": 0,
        }

    asyncio.run(run_test())


@dataclass
class FakeMachine:
    id: str = "node-1"
    workdir: str = "/remote/burner"
    conda_env: str = "ReLL"


class FakeConfig:
    def get_machine(self, machine_id):
        assert machine_id == "node-1"
        return FakeMachine()


class FakeBurn:
    def has_jobs(self, machine_id):
        assert machine_id == "node-1"
        return False


class FakeSSH:
    def __init__(self):
        self.connection = FakeConnection()
        self.process_commands = self.connection.commands
        self.run_commands = []

    def status_for(self, machine_id):
        assert machine_id == "node-1"
        return "connected"

    def get_connection(self, machine_id):
        assert machine_id == "node-1"
        return self.connection

    async def run_command(self, machine_id, cmd):
        assert machine_id == "node-1"
        self.run_commands.append(cmd)
        return "", "", 0


class FakeConnection:
    def __init__(self):
        self.commands = []

    def create_process(self, command):
        self.commands.append(command)
        return FakeProcess()


class FakeProcess:
    exit_status = 0

    def __init__(self):
        self.stdout = FakeStream()
        self.stderr = FakeStream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeTransfer:
    def __init__(self):
        self.copies = []

    async def scp_to_remote(self, machine_id, local_path, remote_path):
        assert machine_id == "node-1"
        assert Path(local_path).exists()
        self.copies.append((str(local_path), remote_path))


class FakeStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration
