import logging
from collections import namedtuple
from dataclasses import dataclass

import psutil
import pytest

from choker.daemon import ChokerDaemon
from choker.daemon import ChokerStrategy
from choker.monitor import CpuLoadMonitor, CpuLoadSample, CpuSnapshot, compute_cpu_load
from choker.runtime import PidStatus, read_pid_status, write_pid_file


@dataclass
class FakeMonitor:
    samples: list[CpuLoadSample]
    requested: list[tuple[float, int | None]]

    def sample(self, window_seconds: float, owned_pid: int | None) -> CpuLoadSample:
        self.requested.append((window_seconds, owned_pid))
        if not self.samples:
            raise AssertionError("unexpected sample")
        return self.samples.pop(0)


class FakeBurner:
    def __init__(self, fail_start: bool = False):
        self.fail_start = fail_start
        self.running = False
        self.starts = 0
        self.stops = 0
        self.pid = 1234
        self.intensities = []

    def start(self) -> None:
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("boom")
        self.intensities.append(1.0)
        self.running = True

    def set_intensity(self, intensity: float) -> None:
        self.starts += 1 if not self.running else 0
        if self.fail_start:
            raise RuntimeError("boom")
        self.intensities.append(intensity)
        self.running = intensity > 0

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def shutdown(self) -> None:
        self.stop()

    def is_running(self) -> bool:
        return self.running

    def owned_pid(self) -> int | None:
        return self.pid if self.running else None


class FakeBackendProcess:
    pid = 5678

    def poll(self):
        return None


class FakeLookbusyBackend:
    def __init__(self):
        self.process_pid = None
        self.intensities = []
        self.stops = 0

    def prepare(self, intensity=0.0):
        self.process_pid = FakeBackendProcess.pid
        self.intensities.append(intensity)

    def set_intensity(self, intensity, elapsed):
        del elapsed
        if self.process_pid is None:
            self.prepare(intensity)
            return
        self.intensities.append(intensity)

    def stop(self):
        self.stops += 1
        self.process_pid = None


def sample(total: float, owned: float, external: float) -> CpuLoadSample:
    return CpuLoadSample(
        total_percent=total,
        owned_percent=owned,
        external_percent=external,
    )


def test_choker_starts_stops_and_resumes_without_duplicate_transitions():
    monitor = FakeMonitor(
        samples=[
            sample(5.0, 0.0, 5.0),
            sample(95.0, 90.0, 5.0),
            sample(100.0, 80.0, 20.0),
            sample(30.0, 0.0, 30.0),
            sample(2.0, 0.0, 2.0),
        ],
        requested=[],
    )
    burner = FakeBurner()
    daemon = ChokerDaemon(
        monitor=monitor,
        burner=burner,
        threshold_percent=10.0,
        window_seconds=0.25,
        strategy=ChokerStrategy.IDLE,
        logger=logging.getLogger("test.choker.transitions"),
    )

    for _ in range(5):
        daemon.step()

    assert burner.starts == 2
    assert burner.stops == 1
    assert burner.running
    assert burner.intensities == [1.0, 1.0]
    assert monitor.requested == [
        (0.25, None),
        (0.25, 1234),
        (0.25, 1234),
        (0.25, None),
        (0.25, None),
    ]


def test_choker_logs_backend_start_failure_and_remains_stopped(caplog):
    monitor = FakeMonitor(samples=[sample(1.0, 0.0, 1.0)], requested=[])
    burner = FakeBurner(fail_start=True)
    daemon = ChokerDaemon(
        monitor=monitor,
        burner=burner,
        threshold_percent=10.0,
        window_seconds=1.0,
        strategy=ChokerStrategy.IDLE,
        logger=logging.getLogger("test.choker.failure"),
    )

    with caplog.at_level(logging.ERROR):
        daemon.step()

    assert burner.starts == 1
    assert not burner.running
    assert "failed to set CPU burn intensity" in caplog.text


def test_choker_shutdown_stops_active_burner_once():
    monitor = FakeMonitor(samples=[sample(1.0, 0.0, 1.0)], requested=[])
    burner = FakeBurner()
    daemon = ChokerDaemon(
        monitor=monitor,
        burner=burner,
        threshold_percent=10.0,
        window_seconds=1.0,
        strategy=ChokerStrategy.IDLE,
        logger=logging.getLogger("test.choker.shutdown"),
    )

    daemon.step()
    daemon.shutdown()

    assert burner.starts == 1
    assert burner.stops == 1
    assert not burner.running


def test_choker_complement_strategy_sets_proportional_intensity():
    monitor = FakeMonitor(
        samples=[
            sample(40.0, 0.0, 40.0),
            sample(100.0, 60.0, 40.0),
            sample(100.0, 20.0, 80.0),
            sample(100.0, 0.0, 100.0),
        ],
        requested=[],
    )
    burner = FakeBurner()
    daemon = ChokerDaemon(
        monitor=monitor,
        burner=burner,
        threshold_percent=10.0,
        target_percent=100.0,
        window_seconds=0.25,
        strategy=ChokerStrategy.COMPLEMENT,
        logger=logging.getLogger("test.choker.complement"),
    )

    for _ in range(4):
        daemon.step()

    assert burner.intensities == pytest.approx([0.6, 0.2])
    assert burner.stops == 1
    assert not burner.running


def test_compute_cpu_load_normalizes_multicore_capacity_to_100_percent():
    result = compute_cpu_load(
        CpuSnapshot(busy_seconds=0.0, total_seconds=0.0, owned_cpu_seconds=0.0),
        CpuSnapshot(busy_seconds=64.0, total_seconds=64.0, owned_cpu_seconds=0.0),
    )

    assert result.total_percent == pytest.approx(100.0)
    assert result.external_percent == pytest.approx(100.0)


def test_compute_cpu_load_subtracts_owned_cpu_and_clamps_external():
    result = compute_cpu_load(
        CpuSnapshot(busy_seconds=0.0, total_seconds=0.0, owned_cpu_seconds=0.0),
        CpuSnapshot(busy_seconds=8.0, total_seconds=8.0, owned_cpu_seconds=10.0),
    )

    assert result.total_percent == pytest.approx(100.0)
    assert result.owned_percent == pytest.approx(100.0)
    assert result.external_percent == pytest.approx(0.0)


def test_compute_cpu_load_treats_exited_owned_process_as_zero_delta():
    result = compute_cpu_load(
        CpuSnapshot(busy_seconds=0.0, total_seconds=0.0, owned_cpu_seconds=4.0),
        CpuSnapshot(busy_seconds=2.0, total_seconds=4.0, owned_cpu_seconds=1.0),
    )

    assert result.total_percent == pytest.approx(50.0)
    assert result.owned_percent == pytest.approx(0.0)
    assert result.external_percent == pytest.approx(50.0)


def test_cpu_monitor_treats_missing_owned_process_as_zero_cpu():
    Times = namedtuple("Times", ["user", "system", "idle"])
    cpu_times = iter(
        [
            Times(user=0.0, system=0.0, idle=0.0),
            Times(user=1.0, system=0.0, idle=1.0),
        ]
    )

    def missing_process(pid):
        raise psutil.NoSuchProcess(pid)

    monitor = CpuLoadMonitor(
        cpu_times_reader=lambda: next(cpu_times),
        process_factory=missing_process,
        sleeper=lambda seconds: None,
    )

    result = monitor.sample(window_seconds=1.0, owned_pid=1234)

    assert result.total_percent == pytest.approx(50.0)
    assert result.owned_percent == pytest.approx(0.0)
    assert result.external_percent == pytest.approx(50.0)


def test_pid_status_reports_stale_pidfile(tmp_path):
    pid_file = tmp_path / "choker.pid"
    write_pid_file(pid_file, 999999999)

    status = read_pid_status(pid_file)

    assert status.state == PidStatus.STALE
    assert status.pid == 999999999


def test_cpu_burn_controller_keeps_backend_warm_until_shutdown():
    from choker.burn import CpuBurnController

    backend = FakeLookbusyBackend()
    burner = CpuBurnController(backend=backend)

    burner.start()
    burner.stop()
    burner.start()
    burner.shutdown()

    assert backend.intensities == [0.0, 1.0, 0.0, 1.0]
    assert backend.stops == 1
