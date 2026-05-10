from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Iterable, Iterator

from .burner_backends import BurnBackend, BurnEvent
from .curve import LoadCurve


def generate_schedule(
    curve: LoadCurve,
    duration: float,
    period: float,
    tick: float,
) -> Iterator[tuple[float, float]]:
    if duration <= 0:
        raise ValueError("duration must be greater than 0")
    if period <= 0:
        raise ValueError("period must be greater than 0")
    if tick <= 0:
        raise ValueError("tick must be greater than 0")

    step = 0
    while True:
        elapsed = step * tick
        if elapsed >= duration:
            break
        yield elapsed, curve.value_at_elapsed(elapsed, period)
        step += 1


def run_schedule(
    curve: LoadCurve,
    duration: float,
    period: float,
    tick: float,
    backends: Iterable[BurnBackend],
    real_time: bool = True,
) -> None:
    backends = list(backends)
    started_at = time.monotonic()
    try:
        for elapsed, intensity in generate_schedule(curve, duration, period, tick):
            if real_time:
                _sleep_until(started_at + elapsed)
            for backend in backends:
                backend.set_intensity(intensity, elapsed)
    finally:
        for backend in backends:
            backend.stop()


def write_events_log(path: str | Path, events: Iterable[BurnEvent]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["backend", "elapsed", "intensity"])
        for event in events:
            writer.writerow(
                [
                    event.backend,
                    f"{event.elapsed:.6f}",
                    f"{event.intensity:.6f}",
                ]
            )


def _sleep_until(deadline: float) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))

