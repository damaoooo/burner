from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import psutil


IDLE_FIELDS = {"idle", "iowait"}


@dataclass(frozen=True)
class CpuSnapshot:
    busy_seconds: float
    total_seconds: float
    owned_cpu_seconds: float


@dataclass(frozen=True)
class CpuLoadSample:
    total_percent: float
    owned_percent: float
    external_percent: float


class CpuLoadMonitor:
    def __init__(
        self,
        cpu_times_reader: Callable[[], object] = psutil.cpu_times,
        process_factory: Callable[[int], object] = psutil.Process,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.cpu_times_reader = cpu_times_reader
        self.process_factory = process_factory
        self.sleeper = sleeper

    def sample(self, window_seconds: float, owned_pid: int | None) -> CpuLoadSample:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than 0")

        start = self._snapshot(owned_pid)
        self.sleeper(window_seconds)
        end = self._snapshot(owned_pid)
        return compute_cpu_load(start, end)

    def _snapshot(self, owned_pid: int | None) -> CpuSnapshot:
        busy_seconds, total_seconds = _read_total_cpu_seconds(self.cpu_times_reader())
        owned_cpu_seconds = (
            self._read_process_tree_cpu_seconds(owned_pid)
            if owned_pid is not None
            else 0.0
        )
        return CpuSnapshot(
            busy_seconds=busy_seconds,
            total_seconds=total_seconds,
            owned_cpu_seconds=owned_cpu_seconds,
        )

    def _read_process_tree_cpu_seconds(self, pid: int) -> float:
        try:
            root = self.process_factory(pid)
        except (psutil.Error, ProcessLookupError):
            return 0.0

        processes = [root]
        try:
            processes.extend(root.children(recursive=True))
        except (psutil.Error, ProcessLookupError, AttributeError):
            pass

        total = 0.0
        for process in processes:
            try:
                times = process.cpu_times()
            except (psutil.Error, ProcessLookupError, AttributeError):
                continue
            total += float(getattr(times, "user", 0.0))
            total += float(getattr(times, "system", 0.0))
        return total


def compute_cpu_load(start: CpuSnapshot, end: CpuSnapshot) -> CpuLoadSample:
    total_delta = max(0.0, end.total_seconds - start.total_seconds)
    busy_delta = max(0.0, end.busy_seconds - start.busy_seconds)
    owned_delta = max(0.0, end.owned_cpu_seconds - start.owned_cpu_seconds)

    if total_delta <= 0:
        return CpuLoadSample(
            total_percent=0.0,
            owned_percent=0.0,
            external_percent=0.0,
        )

    total_percent = _clamp_percent((busy_delta / total_delta) * 100.0)
    owned_percent = _clamp_percent((owned_delta / total_delta) * 100.0)
    external_percent = max(0.0, total_percent - owned_percent)
    return CpuLoadSample(
        total_percent=total_percent,
        owned_percent=owned_percent,
        external_percent=_clamp_percent(external_percent),
    )


def _read_total_cpu_seconds(cpu_times: object) -> tuple[float, float]:
    field_names = getattr(cpu_times, "_fields", None)
    if field_names is None:
        field_names = [
            name
            for name in dir(cpu_times)
            if not name.startswith("_") and isinstance(getattr(cpu_times, name), int | float)
        ]

    total = 0.0
    idle = 0.0
    for name in field_names:
        value = max(0.0, float(getattr(cpu_times, name, 0.0)))
        total += value
        if name in IDLE_FIELDS:
            idle += value

    busy = max(0.0, total - idle)
    return busy, total


def _clamp_percent(value: float) -> float:
    return min(100.0, max(0.0, value))
