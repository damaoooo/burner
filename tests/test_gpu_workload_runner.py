from pathlib import Path

import pytest

import gpu_workloads.runner as runner
from gpu_workloads.runner import ScenarioError, load_scenario, parse_scenario


ROOT = Path(__file__).resolve().parents[1]


def test_default_gpu_scenario_is_valid():
    scenario = load_scenario(ROOT / "UI" / "gpu_scenarios" / "single-gpu-default.json")

    assert scenario.name == "single-gpu-default"
    assert len(scenario.tasks) == 10
    assert scenario.total_duration_seconds > 0
    assert {task.workload for task in scenario.tasks} >= {"gemm", "llm-infer", "video-transcode"}


def test_gpu_scenario_rejects_unknown_workload():
    with pytest.raises(ScenarioError, match="unknown GPU workload"):
        parse_scenario(
            {
                "name": "bad",
                "tasks": [
                    {
                        "workload": "not-real",
                        "duration_seconds": 10,
                        "batch_size": 1,
                    }
                ],
            }
        )


def test_gpu_workload_progress_log_is_time_throttled(monkeypatch, capsys):
    stop = runner.StopFlag()
    times = iter([100.0, 101.0, 106.1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(times))

    runner._log_rate("gemm", 0, 120.0, stop)
    runner._log_rate("gemm", 20, 120.0, stop)
    runner._log_rate("gemm", 40, 120.0, stop)

    output = capsys.readouterr().out
    assert "gemm iterations=0 remaining=20.0s" in output
    assert "gemm iterations=20" not in output
    assert "gemm iterations=40 remaining=13.9s" in output
