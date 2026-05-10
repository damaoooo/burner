from __future__ import annotations

import re
from datetime import datetime, timezone


_DURATION_RE = re.compile(r"^([1-9][0-9]*)([smh])$")


def parse_duration(value: str) -> float:
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid duration '{value}'; expected INTEGER[s|m|h]")

    amount = int(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
    return float(amount * multiplier)


def parse_utc_start(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("start time must be UTC and end with 'Z'")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("start time must use ISO format like 2026-05-10T12:00:00Z") from exc
    return parsed.astimezone(timezone.utc)

