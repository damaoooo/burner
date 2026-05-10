from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class CurveFormatError(ValueError):
    """Raised when a curve CSV cannot be parsed."""


@dataclass(frozen=True)
class CurvePoint:
    x: float
    y: float


class LoadCurve:
    def __init__(self, points: Iterable[CurvePoint]):
        self.points = list(points)
        if len(self.points) < 2:
            raise CurveFormatError("curve must contain at least 2 points")

    @classmethod
    def from_csv(cls, path: str | Path) -> "LoadCurve":
        points: list[CurvePoint] = []
        previous_x: float | None = None
        path = Path(path)

        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for line_number, row in enumerate(reader, start=1):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if len(row) != 2:
                    raise CurveFormatError(
                        f"line {line_number}: expected exactly 2 columns"
                    )
                try:
                    x = float(row[0].strip())
                    y = float(row[1].strip())
                except ValueError as exc:
                    raise CurveFormatError(
                        f"line {line_number}: invalid number"
                    ) from exc

                if x < 0.0 or x > 1.0:
                    raise CurveFormatError(
                        f"line {line_number}: x must be between 0 and 1"
                    )
                if previous_x is not None and x <= previous_x:
                    raise CurveFormatError(
                        f"line {line_number}: x values must be strictly increasing"
                    )

                points.append(CurvePoint(x=x, y=_clamp(y)))
                previous_x = x

        return cls(points)

    def value_at_elapsed(self, elapsed: float, period: float) -> float:
        if period <= 0:
            raise ValueError("period must be greater than 0")
        return self.value_at_fraction((elapsed % period) / period)

    def value_at_fraction(self, fraction: float) -> float:
        if fraction < 0.0 or fraction > 1.0:
            fraction = fraction % 1.0

        for point in self.points:
            if fraction == point.x:
                return point.y

        first = self.points[0]
        last = self.points[-1]

        if fraction < first.x:
            return _interpolate(
                fraction,
                CurvePoint(last.x - 1.0, last.y),
                first,
            )
        if fraction > last.x:
            return _interpolate(
                fraction,
                last,
                CurvePoint(first.x + 1.0, first.y),
            )

        for left, right in zip(self.points, self.points[1:]):
            if left.x <= fraction <= right.x:
                return _interpolate(fraction, left, right)

        return last.y


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _interpolate(x: float, left: CurvePoint, right: CurvePoint) -> float:
    span = right.x - left.x
    if span == 0:
        return right.y
    ratio = (x - left.x) / span
    return left.y + (right.y - left.y) * ratio

