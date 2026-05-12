from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import REPO_ROOT, UI_ROOT


CUSTOM_DIR = UI_ROOT / "waveforms"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class WaveformError(ValueError):
    pass


class WaveformExistsError(WaveformError):
    pass


@dataclass(frozen=True)
class WaveformRecord:
    name: str
    source: str
    points: list[tuple[float, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "points": self.points,
        }


class WaveformStore:
    def __init__(self, custom_dir: Path = CUSTOM_DIR, fixtures_dir: Path = FIXTURES_DIR):
        self.custom_dir = custom_dir
        self.fixtures_dir = fixtures_dir
        self.custom_dir.mkdir(parents=True, exist_ok=True)

    def list_waveforms(self) -> list[WaveformRecord]:
        records: list[WaveformRecord] = []
        records.extend(self._load_dir(self.fixtures_dir, "fixtures"))
        records.extend(self._load_dir(self.custom_dir, "custom"))
        return records

    def get_waveform(self, name: str) -> WaveformRecord:
        _validate_name(name)
        for source, directory in (("custom", self.custom_dir), ("fixtures", self.fixtures_dir)):
            path = directory / f"{name}.csv"
            if path.exists():
                return WaveformRecord(name=name, source=source, points=_read_points(path))
        raise WaveformError(f"unknown waveform: {name}")

    def path_for(self, name: str) -> Path:
        record = self.get_waveform(name)
        directory = self.custom_dir if record.source == "custom" else self.fixtures_dir
        return directory / f"{name}.csv"

    def save_waveform(self, name: str, points: Iterable[tuple[float, float]]) -> WaveformRecord:
        _validate_name(name)
        points = _validate_points(points)
        path = self.custom_dir / f"{name}.csv"
        if path.exists():
            raise WaveformExistsError(f"waveform already exists: {name}")

        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for x, y in points:
                writer.writerow([f"{x:.6f}", f"{y:.6f}"])
        return WaveformRecord(name=name, source="custom", points=points)

    def _load_dir(self, directory: Path, source: str) -> list[WaveformRecord]:
        if not directory.exists():
            return []
        records = []
        for path in sorted(directory.glob("*.csv")):
            records.append(
                WaveformRecord(name=path.stem, source=source, points=_read_points(path))
            )
        return records


def _read_points(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    previous_x: float | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != 2:
                raise WaveformError(f"{path.name}:{line_number}: expected exactly 2 columns")
            try:
                x = float(row[0].strip())
                y = float(row[1].strip())
            except ValueError as exc:
                raise WaveformError(f"{path.name}:{line_number}: invalid number") from exc
            if x < 0.0 or x > 1.0:
                raise WaveformError(f"{path.name}:{line_number}: x must be between 0 and 1")
            if previous_x is not None and x <= previous_x:
                raise WaveformError(f"{path.name}:{line_number}: x must be strictly increasing")
            points.append((x, min(1.0, max(0.0, y))))
            previous_x = x

    if len(points) < 2:
        raise WaveformError(f"{path.name}: waveform must contain at least 2 points")
    return points


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise WaveformError("waveform name may only contain letters, numbers, '_' and '-'")


def _validate_points(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    parsed = [(float(x), float(y)) for x, y in points]
    if len(parsed) < 2:
        raise WaveformError("points must contain at least 2 entries")

    previous_x: float | None = None
    for index, (x, y) in enumerate(parsed):
        if x < 0.0 or x > 1.0:
            raise WaveformError(f"point #{index + 1}: x must be between 0 and 1")
        if y < 0.0 or y > 1.0:
            raise WaveformError(f"point #{index + 1}: y must be between 0 and 1")
        if previous_x is not None and x <= previous_x:
            raise WaveformError(f"point #{index + 1}: x must be strictly increasing")
        previous_x = x
    return parsed
