import os
from pathlib import Path

import pytest

typer = pytest.importorskip("typer")
from typer.testing import CliRunner

from choker.cli import app
from choker.runtime import write_pid_file


def test_choker_cli_rejects_invalid_threshold(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--threshold",
            "101",
            "--pid-file",
            str(tmp_path / "choker.pid"),
            "--log-file",
            str(tmp_path / "choker.log"),
            "--max-iterations",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "threshold must be between 0 and 100" in result.output


def test_choker_cli_rejects_invalid_window(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--window-ms",
            "0",
            "--pid-file",
            str(tmp_path / "choker.pid"),
            "--log-file",
            str(tmp_path / "choker.log"),
            "--max-iterations",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "window-ms must be greater than 0" in result.output


def test_choker_cli_rejects_invalid_target(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--target",
            "101",
            "--pid-file",
            str(tmp_path / "choker.pid"),
            "--log-file",
            str(tmp_path / "choker.log"),
            "--max-iterations",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "target must be between 0 and 100" in result.output


def test_choker_status_reports_stopped(tmp_path):
    result = CliRunner().invoke(
        app,
        ["status", "--pid-file", str(tmp_path / "missing.pid")],
    )

    assert result.exit_code == 0
    assert "stopped" in result.output


def test_choker_status_reports_stale_pidfile(tmp_path):
    pid_file = tmp_path / "choker.pid"
    write_pid_file(pid_file, 999999999)

    result = CliRunner().invoke(app, ["status", "--pid-file", str(pid_file)])

    assert result.exit_code == 0
    assert "stale" in result.output


def test_choker_status_reports_running_pidfile(tmp_path, monkeypatch):
    import choker.runtime as runtime

    pid_file = tmp_path / "choker.pid"
    write_pid_file(pid_file, os.getpid())
    monkeypatch.setattr(runtime, "is_choker_process", lambda pid: True)

    result = CliRunner().invoke(app, ["status", "--pid-file", str(pid_file)])

    assert result.exit_code == 0
    assert "running" in result.output
