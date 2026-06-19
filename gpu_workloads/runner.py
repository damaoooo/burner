from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WORKLOAD_TYPES: tuple[str, ...] = (
    "gemm",
    "memory-bandwidth",
    "cv-train",
    "cv-infer",
    "llm-infer",
    "embedding-infer",
    "diffusion-infer",
    "video-transcode",
    "video-analytics",
    "faiss-search",
)
LOG_INTERVAL_SECONDS = 5.0


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class GpuTask:
    workload: str
    duration_seconds: float
    model: str | None = None
    batch_size: int = 1
    input_shape: tuple[int, ...] = ()
    precision: str = "fp16"
    params: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "workload": self.workload,
            "duration_seconds": self.duration_seconds,
            "batch_size": self.batch_size,
            "input_shape": list(self.input_shape),
            "precision": self.precision,
            "params": dict(self.params or {}),
        }
        if self.model:
            payload["model"] = self.model
        return payload


@dataclass(frozen=True)
class GpuScenario:
    name: str
    tasks: list[GpuTask]

    @property
    def total_duration_seconds(self) -> float:
        return sum(task.duration_seconds for task in self.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_duration_seconds": self.total_duration_seconds,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Docker GPU workload scenarios")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-sequence")
    run_parser.add_argument("--scenario", required=True, help="Scenario JSON path")
    run_parser.add_argument("--gpu", type=int, default=0, help="GPU index visible inside the container")

    args = parser.parse_args(argv)
    if args.command == "run-sequence":
        return run_sequence(Path(args.scenario), args.gpu)
    return 2


def run_sequence(path: Path, gpu: int) -> int:
    scenario = load_scenario(path)
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
    stop = StopFlag()
    stop.install()
    try:
        print(
            f"gpu workload scenario={scenario.name} tasks={len(scenario.tasks)} "
            f"total={scenario.total_duration_seconds:.3f}s gpu={gpu}",
            flush=True,
        )
        for index, task in enumerate(scenario.tasks, start=1):
            if stop.requested:
                break
            print(
                f"task {index}/{len(scenario.tasks)} workload={task.workload} "
                f"duration={task.duration_seconds:.3f}s model={task.model or '-'} "
                f"batch={task.batch_size} precision={task.precision}",
                flush=True,
            )
            start = time.monotonic()
            deadline = start + task.duration_seconds
            _run_task(task, deadline, stop)
            elapsed = time.monotonic() - start
            print(f"task complete workload={task.workload} elapsed={elapsed:.3f}s", flush=True)
        print("gpu workload sequence finished", flush=True)
        return 0
    finally:
        stop.restore()


def load_scenario(path: str | Path) -> GpuScenario:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"invalid GPU scenario JSON: {exc}") from exc
    return parse_scenario(raw)


def parse_scenario(raw: object) -> GpuScenario:
    if not isinstance(raw, dict):
        raise ScenarioError("scenario must be an object")
    name = _required_string(raw, "name")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ScenarioError("scenario tasks must be a non-empty list")
    tasks = [parse_task(item, index) for index, item in enumerate(tasks_raw)]
    return GpuScenario(name=name, tasks=tasks)


def parse_task(raw: object, index: int = 0) -> GpuTask:
    if not isinstance(raw, dict):
        raise ScenarioError(f"task #{index + 1} must be an object")
    workload = _required_string(raw, "workload")
    if workload not in WORKLOAD_TYPES:
        raise ScenarioError(f"unknown GPU workload: {workload}")
    duration = _positive_float(raw.get("duration_seconds"), "duration_seconds")
    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ScenarioError("model must be a non-empty string when provided")
    batch_size = int(raw.get("batch_size", 1))
    if batch_size <= 0:
        raise ScenarioError("batch_size must be greater than 0")
    input_shape = raw.get("input_shape", [])
    if input_shape is None:
        input_shape = []
    if not isinstance(input_shape, list) or not all(isinstance(item, int) and item > 0 for item in input_shape):
        raise ScenarioError("input_shape must be a list of positive integers")
    precision = str(raw.get("precision", "fp16"))
    if precision not in {"fp16", "fp32", "bf16"}:
        raise ScenarioError("precision must be fp16, fp32, or bf16")
    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise ScenarioError("params must be an object")
    return GpuTask(
        workload=workload,
        duration_seconds=duration,
        model=model.strip() if isinstance(model, str) else None,
        batch_size=batch_size,
        input_shape=tuple(input_shape),
        precision=precision,
        params=params,
    )


def _run_task(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    if task.workload == "gemm":
        _gemm(task, deadline, stop)
    elif task.workload == "memory-bandwidth":
        _memory_bandwidth(task, deadline, stop)
    elif task.workload == "cv-train":
        _cv_train(task, deadline, stop)
    elif task.workload == "cv-infer":
        _cv_infer(task, deadline, stop)
    elif task.workload == "llm-infer":
        _llm_infer(task, deadline, stop)
    elif task.workload == "embedding-infer":
        _embedding_infer(task, deadline, stop)
    elif task.workload == "diffusion-infer":
        _diffusion_infer(task, deadline, stop)
    elif task.workload == "video-transcode":
        _video_transcode(task, deadline, stop)
    elif task.workload == "video-analytics":
        _video_analytics(task, deadline, stop)
    elif task.workload == "faiss-search":
        _faiss_search(task, deadline, stop)
    else:
        raise ScenarioError(f"unknown GPU workload: {task.workload}")


def _gemm(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    size = _param_int(task, "matrix_size", 4096)
    left = torch.randn((size, size), device=device, dtype=dtype)
    right = torch.randn((size, size), device=device, dtype=dtype)
    iterations = 0
    while _running(deadline, stop):
        out = left @ right
        if iterations % 8 == 0:
            torch.cuda.synchronize()
            _log_rate("gemm", iterations, deadline, stop)
        left = out
        iterations += 1


def _memory_bandwidth(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    elements = _param_int(task, "elements", 128 * 1024 * 1024)
    source = torch.randn((elements,), device=device, dtype=dtype)
    target = torch.empty_like(source)
    iterations = 0
    while _running(deadline, stop):
        target.copy_(source)
        source.add_(target, alpha=0.0001)
        _ = source.sum()
        if iterations % 16 == 0:
            torch.cuda.synchronize()
            _log_rate("memory-bandwidth", iterations, deadline, stop)
        iterations += 1


def _cv_train(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    torchvision_models = _torchvision_models()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    model_name = task.model or "resnet18"
    model = _torchvision_model(torchvision_models, model_name, weights=True).to(device)
    if dtype in {torch.float16, getattr(torch, "bfloat16", object())}:
        model = model.to(dtype=dtype)
    model.train()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.001)
    height, width = _image_shape(task, default=(224, 224))
    classes = _param_int(task, "classes", 1000)
    iterations = 0
    while _running(deadline, stop):
        inputs = torch.randn((task.batch_size, 3, height, width), device=device, dtype=dtype)
        labels = torch.randint(0, classes, (task.batch_size,), device=device)
        optimizer.zero_grad(set_to_none=True)
        logits = model(inputs)
        loss = torch.nn.functional.cross_entropy(logits.float(), labels)
        loss.backward()
        optimizer.step()
        if iterations % 5 == 0:
            torch.cuda.synchronize()
            _log_rate("cv-train", iterations, deadline, stop)
        iterations += 1


def _cv_infer(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    torchvision_models = _torchvision_models()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    model_name = task.model or "resnet50"
    model = _torchvision_model(torchvision_models, model_name, weights=True).to(device)
    if dtype in {torch.float16, getattr(torch, "bfloat16", object())}:
        model = model.to(dtype=dtype)
    model.eval()
    height, width = _image_shape(task, default=(224, 224))
    inputs = torch.randn((task.batch_size, 3, height, width), device=device, dtype=dtype)
    iterations = 0
    with torch.inference_mode():
        while _running(deadline, stop):
            _ = model(inputs)
            if iterations % 10 == 0:
                torch.cuda.synchronize()
                _log_rate("cv-infer", iterations, deadline, stop)
            iterations += 1


def _llm_infer(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    transformers = _transformers()
    device = _cuda_device(torch)
    model_name = task.model or "distilgpt2"
    dtype = _dtype(torch, task.precision)
    tokenizer = transformers.AutoTokenizer.from_pretrained(model_name)
    tokenizer.pad_token = tokenizer.eos_token
    model = transformers.AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=dtype).to(device)
    model.eval()
    prompt = task.params.get("prompt", "Data center GPU workload simulation") if task.params else "Data center GPU workload simulation"
    max_new_tokens = _param_int(task, "max_new_tokens", 32)
    inputs = tokenizer([prompt] * task.batch_size, return_tensors="pt", padding=True).to(device)
    iterations = 0
    with torch.inference_mode():
        while _running(deadline, stop):
            _ = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=tokenizer.eos_token_id)
            torch.cuda.synchronize()
            _log_rate("llm-infer", iterations, deadline, stop)
            iterations += 1


def _embedding_infer(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    sentence_transformers = _sentence_transformers()
    model_name = task.model or "sentence-transformers/all-MiniLM-L6-v2"
    model = sentence_transformers.SentenceTransformer(model_name, device="cuda")
    texts = [
        f"GPU embedding workload sample sentence {index}"
        for index in range(max(task.batch_size, 1))
    ]
    iterations = 0
    while _running(deadline, stop):
        _ = model.encode(texts, batch_size=task.batch_size, convert_to_tensor=True, normalize_embeddings=True)
        _log_rate("embedding-infer", iterations, deadline, stop)
        iterations += 1


def _diffusion_infer(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    diffusers = _diffusers()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    model_name = task.model or "stabilityai/sd-turbo"
    pipe = diffusers.AutoPipelineForText2Image.from_pretrained(model_name, torch_dtype=dtype).to(device)
    prompt = task.params.get("prompt", "a data center GPU workload dashboard") if task.params else "a data center GPU workload dashboard"
    steps = _param_int(task, "steps", 2)
    height = _param_int(task, "height", 512)
    width = _param_int(task, "width", 512)
    iterations = 0
    while _running(deadline, stop):
        _ = pipe(prompt=[prompt] * task.batch_size, num_inference_steps=steps, height=height, width=width, output_type="np")
        torch.cuda.synchronize()
        _log_rate("diffusion-infer", iterations, deadline, stop)
        iterations += 1


def _video_transcode(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    width = _param_int(task, "width", 1920)
    height = _param_int(task, "height", 1080)
    fps = _param_int(task, "fps", 30)
    codec = str((task.params or {}).get("codec", "h264_nvenc"))
    preset = str((task.params or {}).get("preset", "p4"))
    chunk = min(10.0, max(2.0, task.duration_seconds))
    while _running(deadline, stop):
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=duration={chunk}:size={width}x{height}:rate={fps}",
            "-c:v",
            codec,
            "-preset",
            preset,
            "-f",
            "null",
            "-",
        ]
        _run_subprocess(command, stop)


def _video_analytics(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    torchvision_models = _torchvision_models()
    device = _cuda_device(torch)
    dtype = _dtype(torch, task.precision)
    model = _torchvision_model(torchvision_models, task.model or "resnet18", weights=True).to(device)
    if dtype in {torch.float16, getattr(torch, "bfloat16", object())}:
        model = model.to(dtype=dtype)
    model.eval()
    height, width = _image_shape(task, default=(224, 224))
    frames = torch.randn((task.batch_size, 3, height, width), device=device, dtype=dtype)
    iterations = 0
    with torch.inference_mode():
        while _running(deadline, stop):
            _ = model(frames)
            if iterations % 10 == 0:
                torch.cuda.synchronize()
                _log_rate("video-analytics", iterations, deadline, stop)
            iterations += 1


def _faiss_search(task: GpuTask, deadline: float, stop: "StopFlag") -> None:
    torch = _torch()
    _cuda_device(torch)
    try:
        import faiss  # type: ignore
    except ImportError as exc:
        raise RuntimeError("faiss is not installed in the GPU workload image") from exc
    if not hasattr(faiss, "StandardGpuResources"):
        raise RuntimeError("installed faiss package does not expose GPU resources")
    vectors = _param_int(task, "vectors", 200_000)
    dimension = _param_int(task, "dimension", 768)
    queries = task.batch_size
    resources = faiss.StandardGpuResources()
    cpu_index = faiss.IndexFlatL2(dimension)
    index = faiss.index_cpu_to_gpu(resources, 0, cpu_index)
    import numpy as np

    rng = np.random.default_rng(123)
    xb = rng.random((vectors, dimension), dtype=np.float32)
    xq = rng.random((queries, dimension), dtype=np.float32)
    index.add(xb)
    iterations = 0
    while _running(deadline, stop):
        _ = index.search(xq, 10)
        _log_rate("faiss-search", iterations, deadline, stop)
        iterations += 1


def _torch():
    import torch

    return torch


def _torchvision_models():
    from torchvision import models

    return models


def _transformers():
    import transformers

    return transformers


def _sentence_transformers():
    import sentence_transformers

    return sentence_transformers


def _diffusers():
    import diffusers

    return diffusers


def _cuda_device(torch):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available inside the GPU workload container")
    return torch.device("cuda:0")


def _dtype(torch, precision: str):
    if precision == "fp16":
        return torch.float16
    if precision == "bf16":
        return torch.bfloat16
    return torch.float32


def _torchvision_model(models, name: str, weights: bool):
    factory = getattr(models, name, None)
    if factory is None:
        raise RuntimeError(f"unknown torchvision model: {name}")
    if not weights:
        return factory(weights=None)
    weights_name = f"{name.upper()}_Weights"
    weights_cls = getattr(models, weights_name, None)
    if weights_cls is None:
        return factory(weights=None)
    return factory(weights=weights_cls.DEFAULT)


def _image_shape(task: GpuTask, default: tuple[int, int]) -> tuple[int, int]:
    if len(task.input_shape) >= 2:
        return int(task.input_shape[-2]), int(task.input_shape[-1])
    return default


def _param_int(task: GpuTask, key: str, default: int) -> int:
    value = (task.params or {}).get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"parameter {key} must be an integer") from exc
    if parsed <= 0:
        raise ScenarioError(f"parameter {key} must be greater than 0")
    return parsed


def _run_subprocess(command: list[str], stop: "StopFlag") -> None:
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    while process.poll() is None and not stop.requested:
        time.sleep(0.2)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_float(value: object, key: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"{key} must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ScenarioError(f"{key} must be greater than 0")
    return parsed


def _running(deadline: float, stop: "StopFlag") -> bool:
    return not stop.requested and time.monotonic() < deadline


def _log_rate(label: str, iterations: int, deadline: float, stop: "StopFlag") -> None:
    now = time.monotonic()
    last_log = stop.last_log_times.get(label)
    if iterations == 0 or last_log is None or now - last_log >= LOG_INTERVAL_SECONDS:
        remaining = max(0.0, deadline - now)
        print(f"{label} iterations={iterations} remaining={remaining:.1f}s", flush=True)
        stop.last_log_times[label] = now


class StopFlag:
    def __init__(self) -> None:
        self.requested = False
        self._old_sigterm = None
        self._old_sigint = None
        self.last_log_times: dict[str, float] = {}

    def install(self) -> None:
        self._old_sigterm = signal.signal(signal.SIGTERM, self._request_stop)
        self._old_sigint = signal.signal(signal.SIGINT, self._request_stop)

    def restore(self) -> None:
        if self._old_sigterm is not None:
            signal.signal(signal.SIGTERM, self._old_sigterm)
        if self._old_sigint is not None:
            signal.signal(signal.SIGINT, self._old_sigint)

    def _request_stop(self, signum, frame) -> None:
        del signum, frame
        self.requested = True


if __name__ == "__main__":
    raise SystemExit(main())
