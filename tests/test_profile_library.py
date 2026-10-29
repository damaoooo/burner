import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from profile_library import ProfileError, import_profiles, load_index  # noqa: E402
from waveform_store import WaveformStore  # noqa: E402
from warpper.curve import LoadCurve  # noqa: E402


def write_profiles(path: Path, rows: list[list[float]], header: str = "t_s,cl001,bg001") -> Path:
    lines = [header]
    lines.extend(",".join(f"{value:g}" for value in row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def square_rows(period: float = 1.0, dt: float = 0.002) -> list[list[float]]:
    rows = []
    steps = round(period / dt)
    for index in range(steps + 1):
        t = index * dt
        high = (t % period) < period / 2
        rows.append([t, 4.0 if high else 0.6, 10.0 if not high else 1.5])
    return rows


def test_import_profiles_bins_and_normalizes_each_profile_to_its_own_peak(tmp_path):
    source = write_profiles(tmp_path / "profiles.csv", square_rows())
    dest = tmp_path / "out"

    index = import_profiles(source, dest, resolution_ms=10)

    assert index["period_seconds"] == pytest.approx(1.0)
    assert index["resolution_ms"] == 10
    assert [item["name"] for item in index["profiles"]] == ["cl001", "bg001"]
    assert [item["group"] for item in index["profiles"]] == ["cl", "bg"]
    assert index["profiles"][0]["peak"] == pytest.approx(4.0)
    assert index["profiles"][1]["peak"] == pytest.approx(10.0)

    curve = LoadCurve.from_csv(dest / "cl001.csv")
    assert len(curve.points) == 100
    assert curve.points[0].x == pytest.approx(0.005)
    assert curve.points[-1].x == pytest.approx(0.995)
    assert curve.value_at_fraction(0.25) == pytest.approx(1.0)
    assert curve.value_at_fraction(0.75) == pytest.approx(0.15)

    bg = LoadCurve.from_csv(dest / "bg001.csv")
    assert bg.value_at_fraction(0.25) == pytest.approx(0.15)
    assert bg.value_at_fraction(0.75) == pytest.approx(1.0)


def test_import_profiles_writes_index_with_bounded_preview(tmp_path):
    source = write_profiles(tmp_path / "profiles.csv", square_rows(period=30.0, dt=0.01))
    dest = tmp_path / "out"

    import_profiles(source, dest, resolution_ms=10, preview_points=200)

    index = load_index(dest)
    assert index is not None
    assert json.loads((dest / "index.json").read_text(encoding="utf-8")) == index
    preview = index["profiles"][0]["preview"]
    assert 2 <= len(preview) <= 200
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in preview)


def test_import_profiles_replaces_previous_import(tmp_path):
    dest = tmp_path / "out"
    first = write_profiles(tmp_path / "a.csv", square_rows(), header="t_s,old001,bg001")
    import_profiles(first, dest)
    second = write_profiles(tmp_path / "b.csv", square_rows())

    import_profiles(second, dest)

    assert not (dest / "old001.csv").exists()
    assert (dest / "cl001.csv").exists()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("t_s\n0,1\n", "at least one profile"),
        ("t_s,bad name\n0,1\n1,1\n", "profile name"),
        ("t_s,cl001,cl001\n0,1,1\n1,1,1\n", "duplicate"),
        ("t_s,cl001\n0,1\n0,1\n", "strictly increasing"),
        ("t_s,cl001\n0,1\n1,x\n", "invalid number"),
        ("t_s,cl001\n0,1\n1\n", "expected 2 columns"),
        ("t_s,cl001\n0,-1\n1,1\n", "negative"),
    ],
)
def test_import_profiles_rejects_bad_input(tmp_path, text, message):
    source = tmp_path / "bad.csv"
    source.write_text(text, encoding="utf-8")

    with pytest.raises(ProfileError, match=message):
        import_profiles(source, tmp_path / "out")


def test_import_profiles_rejects_resolution_longer_than_half_period(tmp_path):
    source = write_profiles(tmp_path / "profiles.csv", square_rows(period=0.01, dt=0.002))

    with pytest.raises(ProfileError, match="resolution"):
        import_profiles(source, tmp_path / "out", resolution_ms=10)


def test_waveform_store_lists_profiles_with_preview_and_period(tmp_path):
    profiles_dir = tmp_path / "profiles"
    import_profiles(write_profiles(tmp_path / "p.csv", square_rows()), profiles_dir, preview_points=20)
    store = WaveformStore(
        custom_dir=tmp_path / "custom",
        fixtures_dir=tmp_path / "fixtures",
        profiles_dir=profiles_dir,
    )

    records = [record.to_dict() for record in store.list_waveforms()]

    assert [(item["name"], item["source"]) for item in records] == [
        ("cl001", "profiles"),
        ("bg001", "profiles"),
    ]
    assert records[0]["group"] == "cl"
    assert records[0]["period_seconds"] == pytest.approx(1.0)
    assert len(records[0]["points"]) <= 20


def test_waveform_store_gets_full_profile_and_path(tmp_path):
    profiles_dir = tmp_path / "profiles"
    import_profiles(write_profiles(tmp_path / "p.csv", square_rows()), profiles_dir, preview_points=20)
    store = WaveformStore(
        custom_dir=tmp_path / "custom",
        fixtures_dir=tmp_path / "fixtures",
        profiles_dir=profiles_dir,
    )

    record = store.get_waveform("cl001")

    assert record.source == "profiles"
    assert len(record.points) == 100
    assert len(record.to_dict()["points"]) <= 20
    assert store.path_for("cl001") == profiles_dir / "cl001.csv"


def test_waveform_store_without_profiles_index_lists_nothing_extra(tmp_path):
    store = WaveformStore(
        custom_dir=tmp_path / "custom",
        fixtures_dir=tmp_path / "fixtures",
        profiles_dir=tmp_path / "missing",
    )

    assert store.list_waveforms() == []
