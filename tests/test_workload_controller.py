import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

import workload_controller as workload_module  # noqa: E402
from workload_controller import (  # noqa: E402
    SOURCE_FILES,
    WorkloadController,
    WorkloadError,
    WorkloadScenario,
    WorkloadScenarioJob,
    parse_scenario,
)


def test_parse_scenario_rejects_unknown_workload():
    with pytest.raises(WorkloadError, match="unknown workload"):
        parse_scenario(
            {
                "name": "bad",
                "seed": 1,
                "total_window_seconds": 60,
                "jobs": [
                    {
                        "machine_id": "node-1",
                        "workload": "unknown",
                        "delay_seconds": 0,
                        "duration_seconds": 10,
                        "workers": 1,
                    }
                ],
            }
        )


def test_generate_scenario_is_stable_and_covers_requested_machines(tmp_path, monkeypatch):
    monkeypatch.setattr(workload_module, "SCENARIO_DIR", tmp_path)
    controller = WorkloadController(FakeConfig(["node-1", "node-2"]), FakeSSH(), FakeTransfer(), FakeBurn(), noop_broadcast)

    first = controller.generate_scenario(
        name="server-room",
        machine_ids=["node-1", "node-2"],
        seed=42,
        total_window_seconds=120,
        min_duration_seconds=20,
        max_duration_seconds=40,
        min_workers=1,
        max_workers=3,
    )
    second = controller.generate_scenario(
        name="server-room",
        machine_ids=["node-1", "node-2"],
        seed=42,
        total_window_seconds=120,
        min_duration_seconds=20,
        max_duration_seconds=40,
        min_workers=1,
        max_workers=3,
    )

    assert first.to_dict() == second.to_dict()
    assert [job.machine_id for job in first.jobs] == ["node-1", "node-2"]
    assert all(20 <= job.duration_seconds <= 40 for job in first.jobs)
    assert all(0 <= job.delay_seconds <= 100 for job in first.jobs)
    assert all(1 <= job.workers <= 3 for job in first.jobs)
    assert (tmp_path / "server-room.json").exists()


def test_workload_setup_syncs_sources_and_installs_dependencies():
    async def run_test():
        ssh = FakeSSH()
        transfer = FakeTransfer()
        events = []

        async def broadcast(payload):
            events.append(payload)

        controller = WorkloadController(FakeConfig(["node-1"]), ssh, transfer, FakeBurn(), broadcast)
        machine_ids = await controller.reserve_setup(["node-1"])
        await controller.run_reserved_setup(machine_ids)

        assert {remote for _, remote in transfer.copies} == {
            f"/remote/burner/{relative}" for relative in SOURCE_FILES
        }
        command = "\n".join(ssh.process_commands)
        assert "sudo -n apt-get update" in command
        assert "sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y" in command
        assert "build-essential" in command
        assert events[-1] == {"event": "workload_setup_complete", "exit_code": 0}

    asyncio.run(run_test())


def test_start_scenario_launches_runner_with_process_group():
    async def run_test():
        ssh = FakeSSH(start_pid=4321)
        controller = WorkloadController(FakeConfig(["node-1"]), ssh, FakeTransfer(), FakeBurn(), noop_broadcast)
        scenario = WorkloadScenario(
            name="server-room",
            seed=7,
            total_window_seconds=60,
            jobs=[
                WorkloadScenarioJob(
                    machine_id="node-1",
                    workload="compile",
                    delay_seconds=3,
                    duration_seconds=20,
                    workers=2,
                )
            ],
        )

        jobs = await controller.start_scenario(scenario)

        assert len(jobs) == 1
        assert jobs[0].pid == 4321
        start_command = ssh.run_commands[-1]
        assert "nohup setsid bash -lc" in start_command
        assert "workloads.runner" in start_command
        assert "--workload compile" in start_command
        assert "--workers 2" in start_command
        assert "sleep 3" in start_command
        await controller.stop_workloads(job_ids="all")

    asyncio.run(run_test())


def test_stop_workload_uses_term_then_kill_process_group():
    async def run_test():
        ssh = FakeSSH(start_pid=5678)
        controller = WorkloadController(FakeConfig(["node-1"]), ssh, FakeTransfer(), FakeBurn(), noop_broadcast)
        scenario = WorkloadScenario(
            name="server-room",
            seed=7,
            total_window_seconds=60,
            jobs=[
                WorkloadScenarioJob(
                    machine_id="node-1",
                    workload="crypto",
                    delay_seconds=0,
                    duration_seconds=20,
                    workers=1,
                )
            ],
        )
        await controller.start_scenario(scenario)
        await controller.stop_workloads(job_ids="all")

        stop_command = ssh.run_commands[-1]
        assert "kill -TERM -- \"-$pid\"" in stop_command
        assert "kill -KILL -- \"-$pid\"" in stop_command

    asyncio.run(run_test())


async def noop_broadcast(payload):
    del payload


@dataclass
class FakeMachine:
    id: str
    workdir: str = "/remote/burner"
    conda_env: str = None


class FakeConfig:
    def __init__(self, machine_ids):
        self.machines = {machine_id: FakeMachine(machine_id) for machine_id in machine_ids}

    def list_machines(self):
        return list(self.machines.values())

    def get_machine(self, machine_id):
        return self.machines[machine_id]


class FakeBurn:
    def has_jobs(self, machine_id):
        del machine_id
        return False


class FakeSSH:
    def __init__(self, start_pid=1234):
        self.connection = FakeConnection()
        self.process_commands = self.connection.commands
        self.run_commands = []
        self.start_pid = start_pid

    def status_for(self, machine_id):
        del machine_id
        return "connected"

    def get_connection(self, machine_id):
        del machine_id
        return self.connection

    async def run_command(self, machine_id, cmd):
        del machine_id
        self.run_commands.append(cmd)
        if "nohup setsid" in cmd:
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
        del machine_id
        assert Path(local_path).exists()
        self.copies.append((str(local_path), remote_path))


class FakeStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

