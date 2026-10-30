import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from burn_controller import DELAYED_START_LEAD_SECONDS, BurnController, MachineBurnRequest  # noqa: E402


@dataclass
class FakeMachine:
    id: str
    workdir: str = "/remote/burner"
    conda_env: str = None


class FakeConfig:
    def get_machine(self, machine_id):
        return FakeMachine(machine_id)


class FakeSSH:
    def __init__(self):
        self.commands = []

    def status_for(self, machine_id):
        return "connected"

    async def run_command(self, machine_id, cmd):
        self.commands.append((machine_id, cmd))
        return "4242\n", "", 0


class SlowTransfer:
    """Copying waveforms takes a while, like scp to many nodes."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.done_at = None

    async def scp_to_remote(self, machine_id, local_path, remote_path):
        await asyncio.sleep(self.seconds)
        self.done_at = time.time()


class FakeWaveforms:
    def get_waveform(self, name):
        return object()

    def path_for(self, name):
        return Path("/local") / f"{name}.csv"


async def noop_broadcast(payload):
    pass


def request(machine_id, delay=0.0):
    return MachineBurnRequest(
        id=machine_id,
        enabled=True,
        burn_cpu=True,
        burn_gpu=False,
        delay_seconds=delay,
        waveform_name="cl01",
    )


def test_cpu_engine_is_passed_to_burner():
    async def run_test():
        ssh = FakeSSH()
        controller = BurnController(FakeConfig(), ssh, SlowTransfer(0), FakeWaveforms(), noop_broadcast)

        jobs = await controller.start_burn(
            sync_mode="delayed",
            duration="15s",
            period="15s",
            machines=[request("node-1")],
            tick_seconds=0.01,
            cpu_engine="pwm",
        )
        for job in jobs:
            job.completion_task.cancel()

        launch = next(cmd for _, cmd in ssh.commands if "./burner" in cmd)
        assert "--cpu-engine pwm" in launch

        with pytest.raises(Exception, match="cpu_engine"):
            await controller.start_burn(
                sync_mode="delayed",
                duration="15s",
                period="15s",
                machines=[request("node-2")],
                cpu_engine="turbo",
            )

    asyncio.run(run_test())


def test_delayed_start_is_measured_from_after_the_waveform_copy():
    async def run_test():
        transfer = SlowTransfer(seconds=0.4)
        ssh = FakeSSH()
        controller = BurnController(FakeConfig(), ssh, transfer, FakeWaveforms(), noop_broadcast)

        jobs = await controller.start_burn(
            sync_mode="delayed",
            duration="150s",
            period="150s",
            machines=[request("node-1"), request("node-2"), request("node-3", delay=2.0)],
            tick_seconds=0.01,
        )
        for job in jobs:
            if job.completion_task:
                job.completion_task.cancel()

        starts = {job.machine_id: job.started_at for job in jobs}
        assert starts["node-1"] == starts["node-2"]
        assert starts["node-3"] == pytest.approx(starts["node-1"] + 2.0)
        lead = starts["node-1"] - transfer.done_at
        assert lead == pytest.approx(DELAYED_START_LEAD_SECONDS, abs=0.3)
        launches = [cmd for _, cmd in ssh.commands if "./burner" in cmd]
        assert len(launches) == 3
        assert all("--start" in cmd for cmd in launches)

    asyncio.run(run_test())
