from __future__ import annotations

import csv
import json
import math
import re
import time
from array import array
from pathlib import Path

from config import UI_ROOT


PROFILES_DIR = UI_ROOT / "profiles"
INDEX_NAME = "index.json"
DEFAULT_RESOLUTION_MS = 10
DEFAULT_PREVIEW_POINTS = 750
NORMALIZE_MODES = ("global", "self")
DEFAULT_NORMALIZE = "global"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
GROUP_RE = re.compile(r"^[A-Za-z]+")


class ProfileError(ValueError):
    pass


def import_profiles(
    source: Path,
    dest: Path = PROFILES_DIR,
    resolution_ms: int = DEFAULT_RESOLUTION_MS,
    preview_points: int = DEFAULT_PREVIEW_POINTS,
    source_name: str | None = None,
    normalize: str = DEFAULT_NORMALIZE,
) -> dict[str, object]:
    """Convert a wide power-profile CSV into one normalized curve CSV per profile.

    The first column is time in seconds and spans exactly one period: the last
    row marks the period end and is not part of the curve. Every other column
    is one profile. Samples are averaged into ``resolution_ms`` bins.

    ``normalize="global"`` divides every profile by the highest bin across all
    profiles, so their relative sizes are kept and only the largest profile
    reaches 100% burn. ``normalize="self"`` divides each profile by its own
    peak bin, so every profile peaks at 100%.
    """
    if normalize not in NORMALIZE_MODES:
        raise ProfileError(f"normalize must be one of {', '.join(NORMALIZE_MODES)}")
    if resolution_ms <= 0:
        raise ProfileError("resolution_ms must be greater than 0")
    resolution = resolution_ms / 1000.0

    names, period, sums, counts = _read_binned(source, resolution)
    bin_count = len(counts)

    filled = [index for index in range(bin_count) if counts[index] > 0]
    xs = [(index + 0.5) / bin_count for index in filled]

    columns = [[sums[column][index] / counts[index] for index in filled] for column in range(len(names))]
    global_peak = max(max(values) for values in columns)

    dest.mkdir(parents=True, exist_ok=True)
    _remove_previous_import(dest)
    profiles = []
    for name, values in zip(names, columns):
        peak = max(values)
        scale = global_peak if normalize == "global" else peak
        ys = [value / scale for value in values] if scale > 0 else [0.0 for _ in values]
        _write_curve(dest / f"{name}.csv", xs, ys)
        group = GROUP_RE.match(name)
        profiles.append(
            {
                "name": name,
                "group": group.group(0) if group else "",
                "peak": peak,
                "mean": sum(values) / len(values),
                "preview": _preview(xs, ys, preview_points),
            }
        )

    index = {
        "source": source_name or source.name,
        "imported_at": time.time(),
        "resolution_ms": resolution_ms,
        "period_seconds": period,
        "normalize": normalize,
        "profiles": profiles,
    }
    if normalize == "global":
        index["scale"] = global_peak
    (dest / INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    return index


_index_cache: dict[Path, tuple[int, dict[str, object]]] = {}


def load_index(dest: Path = PROFILES_DIR) -> dict[str, object] | None:
    path = dest / INDEX_NAME
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    cached = _index_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"invalid profile index: {exc}") from exc
    _index_cache[path] = (mtime, index)
    return index


def summarize(index: dict[str, object] | None) -> dict[str, object]:
    if index is None:
        return {"imported": False, "count": 0}
    profiles = index["profiles"]
    groups: dict[str, int] = {}
    for profile in profiles:
        groups[profile["group"]] = groups.get(profile["group"], 0) + 1
    return {
        "imported": True,
        "count": len(profiles),
        "source": index["source"],
        "imported_at": index["imported_at"],
        "resolution_ms": index["resolution_ms"],
        "period_seconds": index["period_seconds"],
        # Imports from before normalization was selectable used "self".
        "normalize": index.get("normalize", "self"),
        "scale": index.get("scale"),
        "groups": groups,
    }


def _read_binned(source: Path, resolution: float) -> tuple[list[str], float, list[array], array]:
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None or len(header) < 2:
            raise ProfileError("profile CSV must have a time column and at least one profile column")
        names = [name.strip() for name in header[1:]]
        seen: set[str] = set()
        for name in names:
            if not NAME_RE.match(name):
                raise ProfileError(
                    f"profile name {name!r} may only contain letters, numbers, '_' and '-'"
                )
            if name in seen:
                raise ProfileError(f"duplicate profile name: {name}")
            seen.add(name)

        width = len(header)
        sums = [array("d") for _ in names]
        counts = array("l")
        t0: float | None = None
        pending: list[float] | None = None
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != width:
                raise ProfileError(f"line {line_number}: expected {width} columns")
            try:
                values = [float(cell) for cell in row]
            except ValueError as exc:
                raise ProfileError(f"line {line_number}: invalid number") from exc
            if not all(math.isfinite(value) for value in values):
                raise ProfileError(f"line {line_number}: invalid number")
            if pending is not None and values[0] <= pending[0]:
                raise ProfileError(f"line {line_number}: time must be strictly increasing")
            if min(values[1:]) < 0:
                raise ProfileError(f"line {line_number}: power must not be negative")
            if t0 is None:
                t0 = values[0]
            # The final row only marks the period end, so a row is binned once
            # the next one has been read.
            if pending is not None:
                _add_to_bin(sums, counts, int((pending[0] - t0) / resolution + 1e-9), pending)
            pending = values

    if t0 is None or pending is None or pending[0] == t0:
        raise ProfileError("profile CSV must contain at least 2 rows")
    period = pending[0] - t0
    bin_count = int(round(period / resolution))
    if bin_count < 2:
        raise ProfileError(
            f"resolution {resolution * 1000:g} ms is too coarse for a {period:g} s profile period"
        )
    # Float jitter can put the last samples one bin past the rounded count.
    while len(counts) > bin_count:
        extra = len(counts) - 1
        counts[extra - 1] += counts.pop()
        for column in sums:
            column[extra - 1] += column.pop()
    _grow(sums, counts, bin_count)
    return names, period, sums, counts


def _add_to_bin(sums: list[array], counts: array, bin_index: int, values: list[float]) -> None:
    _grow(sums, counts, bin_index + 1)
    counts[bin_index] += 1
    for column, value in zip(sums, values[1:]):
        column[bin_index] += value


def _grow(sums: list[array], counts: array, size: int) -> None:
    missing = size - len(counts)
    if missing <= 0:
        return
    counts.extend([0] * missing)
    for column in sums:
        column.extend([0.0] * missing)


def _remove_previous_import(dest: Path) -> None:
    previous = load_index(dest) if (dest / INDEX_NAME).exists() else None
    if previous is None:
        return
    for profile in previous.get("profiles", []):
        name = profile.get("name", "")
        if NAME_RE.match(name):
            (dest / f"{name}.csv").unlink(missing_ok=True)
    (dest / INDEX_NAME).unlink(missing_ok=True)


def _write_curve(path: Path, xs: list[float], ys: list[float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.writelines(f"{x:.8f},{y:.6f}\n" for x, y in zip(xs, ys))


def _preview(xs: list[float], ys: list[float], limit: int) -> list[list[float]]:
    step = max(1, math.ceil(len(xs) / max(2, limit)))
    return [[round(xs[i], 6), round(ys[i], 4)] for i in range(0, len(xs), step)]
