from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .burner_backends import (
    BackendError,
    DutyCycleGpuBackend,
    LookbusyCpuBackend,
    MockBurnBackend,
)
from .burner_core import run_schedule, write_events_log
from .curve import CurveFormatError, LoadCurve
from .timeutil import parse_duration, parse_period_duration, parse_utc_start


DEFAULT_TICK = 0.1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CPU/GPU burn from a CSV curve")
    parser.add_argument("--cpu", action="store_true", help="enable CPU burn")
    parser.add_argument("--gpu", action="store_true", help="enable GPU burn")
    parser.add_argument("-f", "--file", required=True, help="curve CSV path")
    parser.add_argument("-t", "--time", required=True, help="total run time, e.g. 20s")
    parser.add_argument("-p", "--period", required=True, help="curve period, e.g. 60s")
    parser.add_argument("-s", "--start", help="UTC start time, e.g. 2026-05-10T12:00:00Z")
    parser.add_argument("--tick", type=float, default=DEFAULT_TICK, help="scheduler tick seconds")
    parser.add_argument("--mock-backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-schedule", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.cpu and not args.gpu:
        parser.error("at least one of --cpu or --gpu is required")

    try:
        duration = parse_duration(args.time)
        period = parse_period_duration(args.period)
        if args.tick <= 0:
            raise ValueError("tick must be greater than 0")
        curve = LoadCurve.from_csv(args.file)
        start_time = parse_utc_start(args.start) if args.start else None
    except (CurveFormatError, OSError, ValueError) as exc:
        print(f"burner: {exc}", file=sys.stderr)
        return 2

    backends = _build_backends(args)
    _install_signal_handlers()

    try:
        if start_time is not None and not args.no_sleep:
            _wait_until(start_time)
        run_schedule(
            curve=curve,
            duration=duration,
            period=period,
            tick=args.tick,
            backends=backends,
            real_time=not args.no_sleep,
        )
        if args.log_schedule:
            events = []
            for backend in backends:
                events.extend(getattr(backend, "events", []))
            write_events_log(args.log_schedule, events)
    except KeyboardInterrupt:
        return 130
    except BackendError as exc:
        print(f"burner: {exc}", file=sys.stderr)
        return 1

    return 0


def _build_backends(args: argparse.Namespace):
    backends = []
    if args.cpu:
        backends.append(MockBurnBackend("cpu") if args.mock_backend else LookbusyCpuBackend())
    if args.gpu:
        backends.append(MockBurnBackend("gpu") if args.mock_backend else DutyCycleGpuBackend())
    return backends


def _wait_until(start_time: datetime) -> None:
    while True:
        remaining = (start_time - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def _install_signal_handlers() -> None:
    def raise_keyboard_interrupt(signum, frame):
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, raise_keyboard_interrupt)
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
