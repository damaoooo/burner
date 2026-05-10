from pathlib import Path

import pytest

from warpper.curve import CurveFormatError, LoadCurve
from warpper.timeutil import parse_duration


FIXTURES = Path(__file__).parent / "fixtures"


def write_curve(tmp_path, text):
    path = tmp_path / "curve.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_curve_clamps_y_values(tmp_path):
    path = write_curve(tmp_path, "0,-1\n0.5,0.5\n1,2\n")

    curve = LoadCurve.from_csv(path)

    assert [point.y for point in curve.points] == [0.0, 0.5, 1.0]


def test_load_curve_rejects_empty_file(tmp_path):
    path = write_curve(tmp_path, "\n\n")

    with pytest.raises(CurveFormatError, match="at least 2 points"):
        LoadCurve.from_csv(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("0,0,extra\n1,1\n", "exactly 2 columns"),
        ("0,nope\n1,1\n", "invalid number"),
        ("-0.1,0\n1,1\n", "x must be between 0 and 1"),
        ("0,0\n0,1\n", "strictly increasing"),
        ("0.5,0\n0.4,1\n", "strictly increasing"),
    ],
)
def test_load_curve_rejects_invalid_rows(tmp_path, content, message):
    path = write_curve(tmp_path, content)

    with pytest.raises(CurveFormatError, match=message):
        LoadCurve.from_csv(path)


def test_curve_linear_interpolation():
    curve = LoadCurve.from_csv(FIXTURES / "sine.csv")

    assert curve.value_at_fraction(0.0) == pytest.approx(0.5)
    assert curve.value_at_fraction(0.125) == pytest.approx(0.75)
    assert curve.value_at_fraction(0.25) == pytest.approx(1.0)
    assert curve.value_at_fraction(0.625) == pytest.approx(0.25)
    assert curve.value_at_fraction(1.0) == pytest.approx(0.5)


def test_curve_periodic_elapsed_wrap():
    curve = LoadCurve.from_csv(FIXTURES / "sine.csv")

    assert curve.value_at_elapsed(2.5, period=2.0) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("20s", 20.0), ("30m", 1800.0), ("1h", 3600.0)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["1", "0s", "-1s", "1.5s", "1h30m", "abc"])
def test_parse_duration_rejects_invalid_values(text):
    with pytest.raises(ValueError):
        parse_duration(text)
