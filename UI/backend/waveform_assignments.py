from __future__ import annotations

import json
import os
import re
from pathlib import Path

from config import UI_ROOT


ASSIGNMENTS_PATH = UI_ROOT / "state" / "waveform_assignments.json"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class AssignmentError(ValueError):
    pass


def load_assignments(path: Path = ASSIGNMENTS_PATH) -> dict[str, object]:
    """Return the saved per-machine waveform choice, or an empty default."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"use_per_machine": False, "assignments": {}}
    except json.JSONDecodeError as exc:
        raise AssignmentError(f"invalid waveform assignments file: {exc}") from exc
    return {
        "use_per_machine": bool(raw.get("use_per_machine", False)),
        "assignments": dict(raw.get("assignments", {})),
    }


def save_assignments(
    path: Path,
    use_per_machine: bool,
    assignments: dict[str, str],
    known_machines: set[str],
) -> dict[str, object]:
    cleaned: dict[str, str] = {}
    for machine_id, name in sorted(assignments.items()):
        if not name:
            continue
        if machine_id not in known_machines:
            raise AssignmentError(f"unknown machine: {machine_id}")
        if not NAME_RE.match(name):
            raise AssignmentError(f"invalid waveform name for {machine_id}: {name!r}")
        cleaned[machine_id] = name

    record = {"use_per_machine": use_per_machine, "assignments": cleaned}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return record
