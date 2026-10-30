import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from waveform_assignments import (  # noqa: E402
    AssignmentError,
    load_assignments,
    save_assignments,
)


def test_load_assignments_defaults_when_missing(tmp_path):
    assert load_assignments(tmp_path / "missing.json") == {
        "use_per_machine": False,
        "assignments": {},
    }


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "state" / "assignments.json"

    saved = save_assignments(
        path,
        use_per_machine=True,
        assignments={"node-15": "cl003", "node-14": "bg007"},
        known_machines={"node-14", "node-15"},
    )

    assert saved == {
        "use_per_machine": True,
        "assignments": {"node-14": "bg007", "node-15": "cl003"},
    }
    assert load_assignments(path) == saved
    assert json.loads(path.read_text(encoding="utf-8")) == saved


def test_save_drops_empty_names(tmp_path):
    saved = save_assignments(
        tmp_path / "a.json",
        use_per_machine=True,
        assignments={"node-14": "", "node-15": "cl003"},
        known_machines={"node-14", "node-15"},
    )

    assert saved["assignments"] == {"node-15": "cl003"}


@pytest.mark.parametrize(
    ("assignments", "message"),
    [
        ({"node-99": "cl001"}, "unknown machine"),
        ({"node-14": "../etc/passwd"}, "waveform name"),
    ],
)
def test_save_rejects_bad_entries(tmp_path, assignments, message):
    path = tmp_path / "a.json"

    with pytest.raises(AssignmentError, match=message):
        save_assignments(path, True, assignments, known_machines={"node-14"})
    assert not path.exists()


def test_load_rejects_corrupt_file(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(AssignmentError, match="invalid"):
        load_assignments(path)
