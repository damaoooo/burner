from __future__ import annotations

import argparse
import sys

from .watcher_core import CombinedPowerSampler, MockPowerSampler, run_watcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch CPU/GPU power and write CSV")
    parser.add_argument("-n", required=True, help="sample interval in seconds")
    parser.add_argument("-f", "--file", required=True, help="output CSV path")
    parser.add_argument("--mock", action="store_true", help="use generated mock power data")
    parser.add_argument("--samples", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--no-tui", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        interval = float(args.n)
        if interval <= 0:
            raise ValueError("interval must be greater than 0")
        if args.samples is not None and args.samples <= 0:
            raise ValueError("samples must be greater than 0")

        sampler = MockPowerSampler() if args.mock else CombinedPowerSampler()
        run_watcher(
            interval=interval,
            output_path=args.file,
            sampler=sampler,
            max_samples=args.samples,
            tui=not args.no_tui,
        )
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"watcher: {exc}", file=sys.stderr)
        return 2

    return 0

