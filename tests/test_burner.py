import subprocess
import sys
from pathlib import Path

import pytest

import warpper.burner_backends as burner_backends
from warpper.burner_core import generate_schedule, run_schedule
from warpper.burner_backends import DutyCycleGpuBackend, MockBurnBackend
from warpper.curve import LoadCurve


ROOT = Path(__file__).resolve().parents[1]
SINE = ROOT / "tests" / "fixtures" / "sine.csv"


def test_generate_schedule_uses_curve_and_tick():
    curve = LoadCurve.from_csv(SINE)

    schedule = list(generate_schedule(curve, duration=0.5, period=1.0, tick=0.25))

    assert schedule == [
        (0.0, pytest.approx(0.5)),
        (0.25, pytest.approx(1.0)),
    ]


def test_run_schedule_updates_all_backends_and_stops():
    curve = LoadCurve.from_csv(SINE)
    cpu = MockBurnBackend("cpu")
    gpu = MockBurnBackend("gpu")

    run_schedule(
        curve=curve,
        duration=0.5,
        period=1.0,
        tick=0.25,
        backends=[cpu, gpu],
        real_time=False,
    )

    assert [event.intensity for event in cpu.events] == pytest.approx([0.5, 1.0])
    assert [event.intensity for event in gpu.events] == pytest.approx([0.5, 1.0])
    assert cpu.stopped
    assert gpu.stopped


def run_burner_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "burner"), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_burner_cli_requires_cpu_or_gpu():
    result = run_burner_cli("-f", str(SINE), "-t", "1s", "-p", "1s", "--mock-backend")

    assert result.returncode != 0
    assert "at least one of --cpu or --gpu" in result.stderr


def test_burner_cli_mock_backend_writes_schedule_log(tmp_path):
    log_path = tmp_path / "schedule.csv"

    result = run_burner_cli(
        "--cpu",
        "-f",
        str(SINE),
        "-t",
        "1s",
        "-p",
        "1s",
        "--tick",
        "0.25",
        "--mock-backend",
        "--no-sleep",
        "--log-schedule",
        str(log_path),
    )

    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "backend,elapsed,intensity",
        "cpu,0.000000,0.500000",
        "cpu,0.250000,1.000000",
        "cpu,0.500000,0.500000",
        "cpu,0.750000,0.000000",
    ]


def test_gpu_backend_starts_from_gpu_burn_directory(tmp_path, monkeypatch):
    binary = tmp_path / "gpu_burn"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = {}

    class FakeProcess:
        pid = 123

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(burner_backends.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(burner_backends, "_terminate_process_group", lambda process: None)

    backend = DutyCycleGpuBackend(binary=binary)
    backend.set_intensity(1.0, 0.0)
    backend.stop()

    assert calls["command"] == [str(binary), "-stts", "1", "86400"]
    assert calls["kwargs"]["cwd"] == str(tmp_path)
