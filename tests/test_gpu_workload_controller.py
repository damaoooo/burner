import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from gpu_workload_controller import (  # noqa: E402
    DEFAULT_IMAGE,
    SOURCE_FILES,
    GpuWorkloadController,
)


def test_gpu_workload_setup_syncs_sources_and_builds_image():
    async def run_test():
        ssh = FakeSSH()
        transfer = FakeTransfer()
        events = []

        async def broadcast(payload):
            events.append(payload)

        controller = GpuWorkloadController(FakeConfig(), ssh, transfer, FakeJobs(), FakeJobs(), broadcast)
        await controller.reserve_setup("node-1", gpu_index=0, image=DEFAULT_IMAGE)
        await controller.run_reserved_setup("node-1", gpu_index=0, image=DEFAULT_IMAGE)

        assert {remote for _, remote in transfer.copies} == {
            f"/remote/burner/{relative}" for relative in SOURCE_FILES
        }
        command = "\n".join(ssh.process_commands)
        assert "command -v docker" in command
        assert "command -v nvidia-smi" in command
        assert "docker build -t burner-gpu-workloads:latest -f docker/gpu-workloads/Dockerfile ." in command
        assert "docker run --rm --gpus" in command
        assert "device=0" in command
        assert "burner-gpu-workloads:latest" in command
        assert events[-1] == {"event": "gpu_workload_setup_complete", "exit_code": 0}

    asyncio.run(run_test())


def test_gpu_workload_start_launches_single_gpu_container():
    async def run_test():
        ssh = FakeSSH(start_pid=2468)
        controller = GpuWorkloadController(FakeConfig(), ssh, FakeTransfer(), FakeJobs(), FakeJobs(), noop_broadcast)

        job = await controller.start(
            machine_id="node-1",
            scenario_name="single-gpu-default",
            gpu_index=0,
            image=DEFAULT_IMAGE,
        )

        assert job.pid == 2468
        command = ssh.run_commands[-1]
        assert "docker run --rm" in command
        assert "--gpus" in command
        assert "device=0" in command
        assert "-v burner_gpu_cache:/root/.cache" in command
        assert "single-gpu-default.json:/scenario.json:ro" in command
        assert "python3 -m gpu_workloads.runner run-sequence" in command
        await controller.stop(job_ids="all")

    asyncio.run(run_test())


def test_gpu_workload_stop_stops_then_kills_container():
    async def run_test():
        ssh = FakeSSH(start_pid=1357)
        controller = GpuWorkloadController(FakeConfig(), ssh, FakeTransfer(), FakeJobs(), FakeJobs(), noop_broadcast)
        await controller.start("node-1", "single-gpu-default")
        await controller.stop(job_ids="all")

        stop_command = ssh.run_commands[-1]
        assert "docker stop -t 5" in stop_command
        assert "docker kill" in stop_command
        assert "kill 1357" in stop_command

    asyncio.run(run_test())


async def noop_broadcast(payload):
    del payload


@dataclass
class FakeMachine:
    id: str = "node-1"
    workdir: str = "/remote/burner"


class FakeConfig:
    def get_machine(self, machine_id):
        assert machine_id == "node-1"
        return FakeMachine()

    def list_machines(self):
        return [FakeMachine()]


class FakeJobs:
    def has_jobs(self, machine_id):
        assert machine_id == "node-1"
        return False


class FakeSSH:
    def __init__(self, start_pid=1234):
        self.connection = FakeConnection()
        self.process_commands = self.connection.commands
        self.run_commands = []
        self.start_pid = start_pid

    def status_for(self, machine_id):
        assert machine_id == "node-1"
        return "connected"

    def get_connection(self, machine_id):
        assert machine_id == "node-1"
        return self.connection

    async def run_command(self, machine_id, cmd):
        assert machine_id == "node-1"
        self.run_commands.append(cmd)
        if "nohup bash -lc" in cmd:
            return f"{self.start_pid}\n", "", 0
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
