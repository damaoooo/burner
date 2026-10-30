import asyncio
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from power_monitor import (  # noqa: E402
    PowerAggregator,
    PowerMonitor,
    PowerMonitorError,
    parse_sample_line,
    sampler_command,
)


def test_parse_sample_line():
    assert parse_sample_line("1793364420100,183.25") == (1793364420.1, 183.25)
    assert parse_sample_line("garbage") is None
    assert parse_sample_line("") is None


def test_aggregator_sums_nodes_per_time_bin():
    agg = PowerAggregator(["a", "b"], keep_seconds=60)
    agg.add("a", 100.0, 10.0)
    agg.add("b", 100.0, 20.0)
    agg.add("a", 100.1, 11.0)

    rows = agg.finalize(now=100.5, settle=0.2)

    assert rows == [(100.0, 30.0, 2), (100.1, 11.0, 1)]
    assert agg.latest == {"a": 11.0, "b": 20.0}
    assert agg.finalize(now=101.0, settle=0.2) == []


def test_aggregator_waits_for_missing_nodes_until_settled():
    agg = PowerAggregator(["a", "b"], keep_seconds=60)
    agg.add("a", 100.0, 10.0)

    assert agg.finalize(now=100.1, settle=0.5) == []
    agg.add("b", 100.0, 5.0)
    assert agg.finalize(now=100.1, settle=0.5) == [(100.0, 15.0, 2)]


def test_aggregator_ignores_late_samples_for_finalized_bins():
    agg = PowerAggregator(["a"], keep_seconds=60)
    agg.add("a", 100.0, 10.0)
    agg.finalize(now=101.0, settle=0.2)
    agg.add("a", 100.0, 99.0)

    assert agg.finalize(now=102.0, settle=0.2) == []


def test_aggregator_history_and_csv_keep_only_recent_rows():
    agg = PowerAggregator(["a", "b"], keep_seconds=1.0)
    for i in range(30):
        t = 100.0 + i * 0.1
        agg.add("a", t, 1.0)
        agg.add("b", t, 2.0)
    agg.finalize(now=110.0, settle=0.2)

    history = agg.history()
    assert history[0][0] == pytest.approx(101.9)
    assert history[-1] == (pytest.approx(102.9), 3.0, 2)
    lines = agg.to_csv().splitlines()
    assert lines[0] == "epoch_s,total_W,nodes,a,b"
    assert lines[-1] == "102.900,3.00,2,1.00,2.00"


def test_sampler_command_uses_sudo_and_interval():
    cmd = sampler_command(0.1)

    assert cmd.startswith("sudo -n python3 -u -c ")
    assert "0.1" in cmd


class FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            await asyncio.sleep(3600)
        return self._lines.pop(0)


class FakeProcess:
    def __init__(self, lines):
        self.stdout = FakeStream(lines)
        self.stderr = FakeStream([])
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, lines):
        self.lines = lines
        self.commands = []
        self.process = None

    def create_process(self, command):
        self.commands.append(command)
        self.process = FakeProcess(self.lines)
        return self.process


class FakeSSH:
    def __init__(self, connections):
        self.connections = connections

    def status_for(self, machine_id):
        return "connected" if machine_id in self.connections else "disconnected"

    def get_connection(self, machine_id):
        return self.connections[machine_id]


def test_monitor_streams_totals_and_stops():
    async def run_test():
        connections = {
            "a": FakeConnection(["1000000000000,10.0\n", "1000000000100,12.0\n"]),
            "b": FakeConnection(["1000000000000,5.0\n", "1000000000100,6.0\n"]),
        }
        events = []

        async def broadcast(payload):
            events.append(payload)

        monitor = PowerMonitor(FakeSSH(connections), broadcast, clock=lambda: 1000000001.0)
        status = await monitor.start(["a", "b"], interval=0.1)
        assert status["running"] and status["machines"] == ["a", "b"]
        with pytest.raises(PowerMonitorError):
            await monitor.start(["a"], interval=0.1)
        await asyncio.sleep(0.6)
        await monitor.stop()

        samples = [row for event in events if event["event"] == "power_samples" for row in event["samples"]]
        assert samples == [[1000000000.0, 15.0, 2], [1000000000.1, 18.0, 2]]
        assert not monitor.status()["running"]
        assert connections["a"].process.closed
        assert "sudo -n" in connections["a"].commands[0]

    asyncio.run(run_test())


def test_monitor_rejects_disconnected_machines():
    async def run_test():
        monitor = PowerMonitor(FakeSSH({}), lambda payload: None)
        with pytest.raises(PowerMonitorError, match="not connected"):
            await monitor.start(["a"], interval=0.1)

    asyncio.run(run_test())
