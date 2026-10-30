from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .burner_backends import (
    BackendError,
    DutyCycleGpuBackend,
    LookbusyCpuBackend,
    MockBurnBackend,
    PwmCpuBackend,
)
from .burner_core import run_schedule, write_events_log
from .curve import CurveFormatError, LoadCurve
from .timeutil import parse_duration, parse_period_duration, parse_utc_start


DEFAULT_TICK = 0.1
DEFAULT_PREWARM_SECONDS = 2.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CPU/GPU burn from a CSV curve")
    parser.add_argument("--cpu", action="store_true", help="enable CPU burn")
    parser.add_argument("--gpu", action="store_true", help="enable GPU burn")
    parser.add_argument("-f", "--file", required=True, help="curve CSV path")
    parser.add_argument("-t", "--time", required=True, help="total run time, e.g. 20s")
    parser.add_argument("-p", "--period", required=True, help="curve period, e.g. 60s")
    parser.add_argument("-s", "--start", help="UTC start time, e.g. 2026-05-10T12:00:00Z")
    parser.add_argument("--tick", type=float, default=DEFAULT_TICK, help="scheduler tick seconds")
    parser.add_argument(
        "--cpu-engine",
        choices=("lookbusy", "pwm"),
        default="lookbusy",
        help="CPU load generator: lookbusy, or pwm (native/cpu_pwm, all CPUs in step; interval = tick)",
    )
    parser.add_argument(
        "--cpu-kernel",
        choices=("fma", "int"),
        default="fma",
        help="busy loop for --cpu-engine pwm: fma (vector multiply-add, more power) or int",
    )
    parser.add_argument("--mock-backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-schedule", help=argparse.SUPPRESS)
    parser.add_argument("--prewarm-seconds", type=float, default=DEFAULT_PREWARM_SECONDS, help=argparse.SUPPRESS)
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
        if args.prewarm_seconds < 0:
            raise ValueError("prewarm seconds must be non-negative")
        curve = LoadCurve.from_csv(args.file)
        start_time = parse_utc_start(args.start) if args.start else None
    except (CurveFormatError, OSError, ValueError) as exc:
        print(f"burner: {exc}", file=sys.stderr)
        return 2

    backends = _build_backends(args)
    _install_signal_handlers()

    prepared = False
    try:
        if start_time is not None and not args.no_sleep:
            if args.prewarm_seconds > 0:
                _wait_until(start_time - timedelta(seconds=args.prewarm_seconds))
                _prepare_backends(backends)
                prepared = True
            _wait_until(start_time)
        start_elapsed = _late_seconds(start_time)
        if start_elapsed > 0:
            # Launched after the shared start time (slow SSH, busy host): skip
            # ahead so this machine stays in phase with the others.
            print(f"burner: started {start_elapsed:.3f} s late; skipping ahead to keep phase", file=sys.stderr)
        run_schedule(
            curve=curve,
            duration=duration,
            period=period,
            tick=args.tick,
            backends=backends,
            real_time=not args.no_sleep,
            start_elapsed=start_elapsed,
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
    finally:
        if prepared:
            for backend in backends:
                backend.stop()

    return 0


def _build_backends(args: argparse.Namespace):
    backends = []
    if args.cpu:
        if args.mock_backend:
            backends.append(MockBurnBackend("cpu"))
        elif args.cpu_engine == "pwm":
            # cpu_pwm takes whole milliseconds between 1 and 1000.
            interval_ms = min(1000, max(1, round(args.tick * 1000)))
            backends.append(PwmCpuBackend(interval_ms=interval_ms, kernel=args.cpu_kernel))
        else:
            backends.append(LookbusyCpuBackend())
    if args.gpu:
        backends.append(MockBurnBackend("gpu") if args.mock_backend else DutyCycleGpuBackend())
    return backends


def _late_seconds(start_time: datetime | None) -> float:
    if start_time is None:
        return 0.0
    # Under 1 ms is wait/scheduling jitter, not a late launch.
    late = (datetime.now(timezone.utc) - start_time).total_seconds()
    return late if late > 0.001 else 0.0


def _wait_until(start_time: datetime) -> None:
    while True:
        remaining = (start_time - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def _prepare_backends(backends) -> None:
    prepared = []
    try:
        for backend in backends:
            backend.prepare(0.0)
            prepared.append(backend)
    except Exception:
        for backend in prepared:
            backend.stop()
        raise


def _install_signal_handlers() -> None:
    def raise_keyboard_interrupt(signum, frame):
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, raise_keyboard_interrupt)
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
