import argparse
import json
from pathlib import Path

import pytest

import gpu_workloads.cli as cli


def test_run_task_builds_single_task_scenario(monkeypatch):
    captured = {}

    def fake_run_local(scenario, gpu, image, container_name):
        scenario_path = Path(scenario)
        captured["scenario"] = json.loads(scenario_path.read_text(encoding="utf-8"))
        captured["exists_during_call"] = scenario_path.exists()
        captured["gpu"] = gpu
        captured["image"] = image
        captured["container_name"] = container_name
        return 0

    monkeypatch.setattr(cli, "run_local", fake_run_local)

    exit_code = cli.main(
        [
            "run-task",
            "gemm",
            "--duration",
            "1m",
            "--gpu",
            "0",
            "--image",
            "custom:latest",
            "--container-name",
            "gpu_gemm",
            "--batch-size",
            "2",
            "--precision",
            "fp32",
            "--matrix-size",
            "8192",
            "--param",
            "warmup=3",
        ]
    )

    assert exit_code == 0
    assert captured["exists_during_call"] is True
    assert captured["gpu"] == 0
    assert captured["image"] == "custom:latest"
    assert captured["container_name"] == "gpu_gemm"
    assert captured["scenario"] == {
        "name": "single-gemm",
        "tasks": [
            {
                "workload": "gemm",
                "duration_seconds": 60.0,
                "batch_size": 2,
                "input_shape": [],
                "precision": "fp32",
                "params": {"warmup": 3, "matrix_size": 8192},
            }
        ],
    }


def test_run_task_parses_model_shape_and_string_params(monkeypatch):
    captured = {}

    def fake_run_local(scenario, gpu, image, container_name):
        del gpu, image, container_name
        captured["scenario"] = json.loads(Path(scenario).read_text(encoding="utf-8"))
        return 0

    monkeypatch.setattr(cli, "run_local", fake_run_local)

    exit_code = cli.main(
        [
            "run-task",
            "cv-infer",
            "--duration",
            "90s",
            "--model",
            "resnet50",
            "--batch-size",
            "32",
            "--input-shape",
            "3x224x224",
            "--prompt",
            "ignored by cv-infer but accepted",
        ]
    )

    assert exit_code == 0
    task = captured["scenario"]["tasks"][0]
    assert task["duration_seconds"] == 90.0
    assert task["model"] == "resnet50"
    assert task["batch_size"] == 32
    assert task["input_shape"] == [3, 224, 224]
    assert task["params"] == {"prompt": "ignored by cv-infer but accepted"}


def test_cli_parsers_reject_invalid_values():
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_duration_seconds("10days")
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_input_shape("3x0x224")
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_param_assignment("missing_equals")
