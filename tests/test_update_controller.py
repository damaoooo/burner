import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from update_controller import UpdateController  # noqa: E402


def test_update_controller_resets_submodules_before_pull_and_build():
    async def run_test():
        events = []

        async def broadcast(payload):
            events.append(payload)

        ssh = FakeSSH()
        controller = UpdateController(FakeConfig(), ssh, FakeBurn(), broadcast)

        await controller.run_update("node-1", has_gpu=True)

        command = ssh.connection.commands[0]
        assert "git reset --hard HEAD" in command
        assert "git submodule foreach --recursive" in command
        assert command.index("git reset --hard HEAD") < command.index("git pull --recurse-submodules")
        assert "git submodule sync --recursive" in command
        assert "git submodule update --init --recursive --force" in command
        assert command.index("git submodule update --init --recursive --force") < command.index("bash scripts/build_lookbusy.sh")
        assert "bash scripts/build_gpu_burn.sh" in command
        assert events[-1] == {"event": "update_done", "id": "node-1", "exit_code": 0}

    asyncio.run(run_test())


@dataclass
class FakeMachine:
    id: str = "node-1"
    workdir: str = "/remote/burner"
    conda_env: str = None


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

    def get_connection(self, machine_id):
        assert machine_id == "node-1"
        return self.connection


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


class FakeStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration
