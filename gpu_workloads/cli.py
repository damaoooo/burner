from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from gpu_workloads.runner import WORKLOAD_TYPES, parse_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "burner-gpu-workloads:latest"
DEFAULT_CONTAINER = "burner_gpu_workload_local"
KNOWN_PARAM_ARGS: tuple[tuple[str, str, type], ...] = (
    ("matrix_size", "--matrix-size", int),
    ("elements", "--elements", int),
    ("classes", "--classes", int),
    ("prompt", "--prompt", str),
    ("max_new_tokens", "--max-new-tokens", int),
    ("steps", "--steps", int),
    ("height", "--height", int),
    ("width", "--width", int),
    ("fps", "--fps", int),
    ("codec", "--codec", str),
    ("preset", "--preset", str),
    ("vectors", "--vectors", int),
    ("dimension", "--dimension", int),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Docker GPU workload helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-image")
    build_parser.add_argument("--image", default=DEFAULT_IMAGE)
    build_parser.add_argument("--no-cache", action="store_true")

    run_parser = subparsers.add_parser("run-local")
    run_parser.add_argument("--scenario", required=True)
    run_parser.add_argument("--gpu", type=int, default=0)
    run_parser.add_argument("--image", default=DEFAULT_IMAGE)
    run_parser.add_argument("--container-name", default=DEFAULT_CONTAINER)

    task_parser = subparsers.add_parser("run-task")
    task_parser.add_argument("workload", choices=WORKLOAD_TYPES)
    task_parser.add_argument("--duration", required=True, type=parse_duration_seconds)
    task_parser.add_argument("--gpu", type=int, default=0)
    task_parser.add_argument("--image", default=DEFAULT_IMAGE)
    task_parser.add_argument("--container-name", default=DEFAULT_CONTAINER)
    task_parser.add_argument("--model")
    task_parser.add_argument("--batch-size", type=int, default=1)
    task_parser.add_argument("--input-shape", type=parse_input_shape, default=[])
    task_parser.add_argument("--precision", choices=("fp16", "fp32", "bf16"), default="fp16")
    task_parser.add_argument(
        "--param",
        action="append",
        type=parse_param_assignment,
        default=[],
        metavar="KEY=VALUE",
        help="Extra workload parameter. Values are parsed as numbers when possible.",
    )
    for _, flag, value_type in KNOWN_PARAM_ARGS:
        task_parser.add_argument(flag, type=value_type)

    stop_parser = subparsers.add_parser("stop-local")
    stop_parser.add_argument("--container-name", default=DEFAULT_CONTAINER)

    args = parser.parse_args(argv)
    if args.command == "build-image":
        return build_image(args.image, no_cache=args.no_cache)
    if args.command == "run-local":
        return run_local(
            scenario=Path(args.scenario),
            gpu=args.gpu,
            image=args.image,
            container_name=args.container_name,
        )
    if args.command == "run-task":
        return run_task(
            workload=args.workload,
            duration_seconds=args.duration,
            gpu=args.gpu,
            image=args.image,
            container_name=args.container_name,
            model=args.model,
            batch_size=args.batch_size,
            input_shape=args.input_shape,
            precision=args.precision,
            params=task_params_from_args(args),
        )
    if args.command == "stop-local":
        return stop_local(args.container_name)
    return 2


def build_image(image: str = DEFAULT_IMAGE, no_cache: bool = False) -> int:
    command = [
        "docker",
        "build",
        "-t",
        image,
        "-f",
        str(PROJECT_ROOT / "docker" / "gpu-workloads" / "Dockerfile"),
    ]
    if no_cache:
        command.append("--no-cache")
    command.append(str(PROJECT_ROOT))
    return subprocess.call(command)


def run_local(
    scenario: Path,
    gpu: int = 0,
    image: str = DEFAULT_IMAGE,
    container_name: str = DEFAULT_CONTAINER,
) -> int:
    scenario = scenario.resolve()
    if not scenario.exists():
        raise SystemExit(f"scenario not found: {scenario}")
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--gpus",
        f"device={gpu}",
        "-v",
        "burner_gpu_cache:/root/.cache",
        "-v",
        f"{scenario}:/scenario.json:ro",
        image,
        "python3",
        "-m",
        "gpu_workloads.runner",
        "run-sequence",
        "--scenario",
        "/scenario.json",
        "--gpu",
        "0",
    ]
    return subprocess.call(command)


def run_task(
    workload: str,
    duration_seconds: float,
    gpu: int = 0,
    image: str = DEFAULT_IMAGE,
    container_name: str = DEFAULT_CONTAINER,
    model: str | None = None,
    batch_size: int = 1,
    input_shape: list[int] | None = None,
    precision: str = "fp16",
    params: dict[str, object] | None = None,
) -> int:
    scenario = single_task_scenario(
        workload=workload,
        duration_seconds=duration_seconds,
        model=model,
        batch_size=batch_size,
        input_shape=input_shape or [],
        precision=precision,
        params=params or {},
    )
    with tempfile.TemporaryDirectory(prefix="burner-gpu-task-") as directory:
        scenario_path = Path(directory) / "scenario.json"
        scenario_path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")
        return run_local(
            scenario=scenario_path,
            gpu=gpu,
            image=image,
            container_name=container_name,
        )


def single_task_scenario(
    workload: str,
    duration_seconds: float,
    model: str | None = None,
    batch_size: int = 1,
    input_shape: list[int] | None = None,
    precision: str = "fp16",
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    task: dict[str, object] = {
        "workload": workload,
        "duration_seconds": duration_seconds,
        "batch_size": batch_size,
        "input_shape": input_shape or [],
        "precision": precision,
        "params": params or {},
    }
    if model:
        task["model"] = model
    scenario = {"name": f"single-{workload}", "tasks": [task]}
    parse_scenario(scenario)
    return scenario


def stop_local(container_name: str = DEFAULT_CONTAINER) -> int:
    subprocess.call(["docker", "stop", "-t", "5", container_name])
    return subprocess.call(["docker", "kill", container_name])


def parse_duration_seconds(raw: str) -> float:
    text = str(raw).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", text)
    if not match:
        raise argparse.ArgumentTypeError("duration must be a positive number, optionally suffixed with s, m, or h")
    value = float(match.group(1))
    suffix = match.group(2)
    multipliers = {
        "": 1,
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "hours": 3600,
    }
    if suffix not in multipliers:
        raise argparse.ArgumentTypeError("duration suffix must be s, m, or h")
    seconds = value * multipliers[suffix]
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than 0")
    return seconds


def parse_input_shape(raw: str) -> list[int]:
    text = raw.strip().lower()
    if not text:
        return []
    parts = [part for part in re.split(r"[x,]", text) if part]
    try:
        shape = [int(part) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("input shape must look like 3,224,224 or 3x224x224") from exc
    if any(part <= 0 for part in shape):
        raise argparse.ArgumentTypeError("input shape values must be greater than 0")
    return shape


def task_params_from_args(args: argparse.Namespace) -> dict[str, object]:
    params: dict[str, object] = {}
    for key, value in args.param:
        params[key] = value
    for key, _, _ in KNOWN_PARAM_ARGS:
        value = getattr(args, key)
        if value is not None:
            params[key] = value
    return params


def parse_param_assignment(raw: str) -> tuple[str, object]:
    key, separator, value = raw.partition("=")
    if not separator or not key.strip():
        raise argparse.ArgumentTypeError("--param must look like KEY=VALUE")
    return key.strip(), parse_scalar(value.strip())


def parse_scalar(raw: str) -> object:
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


if __name__ == "__main__":
    raise SystemExit(main())
