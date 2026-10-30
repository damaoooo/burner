#!/usr/bin/env python3
"""Import a wide power-profile CSV into the WebUI profile library."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "UI" / "backend"))

from profile_library import (  # noqa: E402
    DEFAULT_NORMALIZE,
    DEFAULT_RESOLUTION_MS,
    NORMALIZE_MODES,
    PROFILES_DIR,
    ProfileError,
    import_profiles,
    summarize,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="wide CSV: time column, then one column per profile")
    parser.add_argument("--resolution-ms", type=int, default=DEFAULT_RESOLUTION_MS)
    parser.add_argument("--dest", type=Path, default=PROFILES_DIR)
    parser.add_argument(
        "--normalize",
        choices=NORMALIZE_MODES,
        default=DEFAULT_NORMALIZE,
        help="global: divide every profile by the highest peak (keeps relative sizes); "
        "self: divide each profile by its own peak",
    )
    args = parser.parse_args(argv)
    try:
        index = import_profiles(args.csv, args.dest, args.resolution_ms, normalize=args.normalize)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = summarize(index)
    groups = ", ".join(f"{name}={count}" for name, count in summary["groups"].items())
    print(
        f"imported {summary['count']} profiles ({groups}), period {summary['period_seconds']:g}s, "
        f"resolution {summary['resolution_ms']} ms, normalize {summary['normalize']} -> {args.dest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
