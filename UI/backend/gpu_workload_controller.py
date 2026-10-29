from __future__ import annotations

import asyncio
import re
import shlex
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from config import REPO_ROOT, UI_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gpu_workloads.runner import GpuScenario, ScenarioError, load_scenario


GPU_SCENARIO_DIR = UI_ROOT / "gpu_scenarios"
DEFAULT_IMAGE = "burner-gpu-workloads:latest"
DOCKER = "DOCKER_HOST=unix:///var/run/docker.sock docker"
SOURCE_FILES: tuple[str, ...] = (
    "docker/gpu-workloads/Dockerfile",
    "gpu_workloads/__init__.py",
    "gpu_workloads/runner.py",
    "gpu_workloads/cli.py",
)
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

Broadcast = Callable[[dict[str, object]], Awaitable[None]]


class GpuWorkloadError(RuntimeError):
    pass


class GpuWorkloadConflictError(GpuWorkloadError):
    pass


class ConfigLike(Protocol):
    def list_machines(self): ...
    def get_machine(self, machine_id: str): ...


class SSHLike(Protocol):
    def status_for(self, machine_id: str) -> str: ...
    def get_connection(self, machine_id: str): ...
    async def run_command(self, machine_id: str, cmd: str) -> tuple[str, str, int]: ...


class FileTransferLike(Protocol):
    async def scp_to_remote(
        self,
        machine_id: str,
        local_path: str | Path,
        remote_path: str,
    ) -> None: ...


class JobLike(Protocol):
    def has_jobs(self, machine_id: str) -> bool: ...


@dataclass
class GpuSetupStatus:
    machine_id: str
    status: str = "queued"
    step: str = "queued"
    exit_code: int | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.machine_id,
            "status": self.status,
            "step": self.step,
        }
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.message:
            payload["message"] = self.message
        return payload


@dataclass
class GpuWorkloadJobInfo:
    job_id: str
    machine_id: str
    scenario_name: str
    pid: int
    container_name: str
    image: str
    gpu_index: int
    started_at: float
    duration_seconds: float
    log_path: str
    scenario_path: str
    completion_task: asyncio.Task[None] | None = None

    def to_dict(self) -> dict[str, object]:
        elapsed = max(0.0, time.time() - self.started_at)
        return {
            "job_id": self.job_id,
            "machine_id": self.machine_id,
            "scenario_name": self.scenario_name,
            "pid": self.pid,
            "container_name": self.container_name,
            "image": self.image,
            "gpu_index": self.gpu_index,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "elapsed_seconds": elapsed,
            "log_path": self.log_path,
        }


class GpuWorkloadController:
    def __init__(
        self,
        config: ConfigLike,
        ssh: SSHLike,
        file_transfer: FileTransferLike,
        burn: JobLike,
        cpu_workload: JobLike,
        broadcast: Broadcast,
    ):
        self._config = config
        self._ssh = ssh
        self._file_transfer = file_transfer
        self._burn = burn
        self._cpu_workload = cpu_workload
        self._broadcast = broadcast
        self.job_registry: dict[str, GpuWorkloadJobInfo] = {}
        self._setup_running = False
        self._setup_status: dict[str, GpuSetupStatus] = {}
        self._setup_lock = asyncio.Lock()

    def list_scenarios(self) -> list[dict[str, object]]:
        GPU_SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        for path in sorted(GPU_SCENARIO_DIR.glob("*.json")):
            try:
                scenario = load_scenario(path)
            except (OSError, ScenarioError):
                continue
            records.append(
                {
                    "name": scenario.name,
                    "tasks": len(scenario.tasks),
                    "total_duration_seconds": scenario.total_duration_seconds,
                }
            )
        return records

    def get_scenario(self, name: str) -> GpuScenario:
        return _load_named_scenario(name)

    async def reserve_setup(
        self,
        machine_id: str,
        gpu_index: int = 0,
        image: str = DEFAULT_IMAGE,
        no_cache: bool = False,
    ) -> None:
        del gpu_index, image, no_cache
        self._config.get_machine(machine_id)
        async with self._setup_lock:
            if self._setup_running:
                raise GpuWorkloadConflictError("GPU workload setup is already running")
            if self._ssh.status_for(machine_id) != "connected":
                raise GpuWorkloadError(f"machine {machine_id} is not connected")
            if self._burn.has_jobs(machine_id) or self._cpu_workload.has_jobs(machine_id) or self.has_jobs(machine_id):
                raise GpuWorkloadConflictError(f"machine {machine_id} is currently running a job")
            self._setup_running = True
            self._setup_status = {machine_id: GpuSetupStatus(machine_id)}

    async def run_reserved_setup(
        self,
        machine_id: str,
        gpu_index: int = 0,
        image: str = DEFAULT_IMAGE,
        no_cache: bool = False,
    ) -> None:
        exit_code = 0
        try:
            result = await self._run_setup_machine(machine_id, gpu_index, image, no_cache)
            if result != 0:
                exit_code = 1
        finally:
            self._setup_running = False
            await self._broadcast(
                {
                    "event": "gpu_workload_setup_complete",
                    "exit_code": exit_code,
                }
            )

    async def start(
        self,
        machine_id: str,
        scenario_name: str,
        gpu_index: int = 0,
        image: str = DEFAULT_IMAGE,
    ) -> GpuWorkloadJobInfo:
        if self._setup_running:
            raise GpuWorkloadConflictError("GPU workload setup is currently running")
        self._config.get_machine(machine_id)
        if self._ssh.status_for(machine_id) != "connected":
            raise GpuWorkloadError(f"machine {machine_id} is not connected")
        if self._burn.has_jobs(machine_id):
            raise GpuWorkloadConflictError(f"machine {machine_id} is currently burning")
        if self._cpu_workload.has_jobs(machine_id) or self.has_jobs(machine_id):
            raise GpuWorkloadConflictError(f"machine {machine_id} is currently running a workload")
        scenario = self.get_scenario(scenario_name)
        await self._copy_scenario(machine_id, scenario_name)
        return await self._start_single(machine_id, scenario, gpu_index, image)

    async def stop(
        self,
        machine_ids: list[str] | Literal["all"] | None = None,
        job_ids: list[str] | Literal["all"] | None = None,
    ) -> None:
        ids = self._resolve_stop_job_ids(machine_ids, job_ids)
        await asyncio.gather(*(self._stop_single(job_id) for job_id in ids))

    def status(self) -> list[dict[str, object]]:
        return [job.to_dict() for job in self.job_registry.values()]

    def setup_status(self) -> dict[str, object]:
        return {
            "running": self._setup_running,
            "machines": [status.to_dict() for status in self._setup_status.values()],
        }

    def has_jobs(self, machine_id: str) -> bool:
        return any(job.machine_id == machine_id for job in self.job_registry.values())

    async def _run_setup_machine(
        self,
        machine_id: str,
        gpu_index: int,
        image: str,
        no_cache: bool,
    ) -> int:
        try:
            await self._sync_sources(machine_id)
            await self._run_setup_command(machine_id, gpu_index, image, no_cache)
            await self._finish_setup(machine_id, exit_code=0)
            return 0
        except Exception as exc:
            await self._log_setup(machine_id, f"gpu workload setup failed: {exc}")
            await self._finish_setup(machine_id, exit_code=1, message=str(exc))
            return 1

    async def _sync_sources(self, machine_id: str) -> None:
        machine = self._config.get_machine(machine_id)
        await self._set_setup_progress(machine_id, "sync", "running")
        remote_dirs = sorted({str(Path(machine.workdir) / Path(path).parent) for path in SOURCE_FILES})
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(path) for path in remote_dirs)
        stdout, stderr, exit_code = await self._ssh.run_command(machine_id, mkdir_cmd)
        if exit_code != 0:
            raise GpuWorkloadError(f"sync mkdir failed with exit code {exit_code}: {stderr.strip()}")
        for line in (stdout.strip(), stderr.strip()):
            if line:
                await self._log_setup(machine_id, line)
        for relative in SOURCE_FILES:
            await self._log_setup(machine_id, f"[sync] scp {relative}")
            await self._file_transfer.scp_to_remote(
                machine_id,
                REPO_ROOT / relative,
                str(Path(machine.workdir) / relative),
            )

    async def _run_setup_command(
        self,
        machine_id: str,
        gpu_index: int,
        image: str,
        no_cache: bool,
    ) -> None:
        machine = self._config.get_machine(machine_id)
        await self._set_setup_progress(machine_id, "docker-build", "running")
        command = _setup_command(image, gpu_index, no_cache)
        full_cmd = f"bash -lc {shlex.quote(f'cd {shlex.quote(machine.workdir)} && {command}')}"
        conn = self._ssh.get_connection(machine_id)
        async with conn.create_process(full_cmd) as process:
            await asyncio.gather(
                self._stream_setup_lines(machine_id, process.stdout),
                self._stream_setup_lines(machine_id, process.stderr),
            )
            exit_status = process.exit_status
        if exit_status != 0:
            raise GpuWorkloadError(f"GPU workload setup failed with exit code {exit_status}")

    async def _copy_scenario(self, machine_id: str, scenario_name: str) -> str:
        machine = self._config.get_machine(machine_id)
        local_path = _scenario_path(scenario_name)
        remote_dir = str(Path(machine.workdir) / "ui_gpu_scenarios")
        stdout, stderr, exit_code = await self._ssh.run_command(machine_id, f"mkdir -p {shlex.quote(remote_dir)}")
        if exit_code != 0:
            raise GpuWorkloadError(f"scenario mkdir failed with exit code {exit_code}: {stderr.strip()}")
        if stdout.strip():
            await self._log_setup(machine_id, stdout.strip())
        remote_path = str(Path(remote_dir) / local_path.name)
        await self._file_transfer.scp_to_remote(machine_id, local_path, remote_path)
        return remote_path

    async def _start_single(
        self,
        machine_id: str,
        scenario: GpuScenario,
        gpu_index: int,
        image: str,
    ) -> GpuWorkloadJobInfo:
        machine = self._config.get_machine(machine_id)
        job_id = f"{machine_id}-{uuid.uuid4().hex[:12]}"
        container_name = f"burner_gpu_workload_{job_id.replace('-', '_')}"
        log_path = f"/tmp/burner_gpu_workload_{job_id}.log"
        remote_scenario = str(Path(machine.workdir) / "ui_gpu_scenarios" / f"{scenario.name}.json")
        runner_args = [
            "python3",
            "-m",
            "gpu_workloads.runner",
            "run-sequence",
            "--scenario",
            "/scenario.json",
            "--gpu",
            "0",
        ]
        docker_command = " ".join(
            [
                f"{DOCKER} run --rm",
                f"--name {shlex.quote(container_name)}",
                f"--gpus {_docker_gpu_shell_arg(gpu_index)}",
                "-v burner_gpu_cache:/root/.cache",
                f"-v {shlex.quote(remote_scenario + ':/scenario.json:ro')}",
                shlex.quote(image),
                shlex.join(runner_args),
            ]
        )
        runner_script = "\n".join(
            [
                "set -euo pipefail",
                f"{DOCKER} rm -f {shlex.quote(container_name)} >/dev/null 2>&1 || true",
                f"exec {docker_command}",
            ]
        )
        inner = (
            f"cd {shlex.quote(machine.workdir)} || exit 1; "
            f"nohup bash -lc {shlex.quote(runner_script)} "
            f"> {shlex.quote(log_path)} 2>&1 < /dev/null & echo $!"
        )
        stdout, stderr, exit_code = await self._ssh.run_command(machine_id, f"bash -lc {shlex.quote(inner)}")
        if exit_code != 0:
            raise GpuWorkloadError(f"{machine_id}: failed to start GPU workload: {stderr.strip()}")
        pid = _parse_pid(stdout)
        job = GpuWorkloadJobInfo(
            job_id=job_id,
            machine_id=machine_id,
            scenario_name=scenario.name,
            pid=pid,
            container_name=container_name,
            image=image,
            gpu_index=gpu_index,
            started_at=time.time(),
            duration_seconds=scenario.total_duration_seconds,
            log_path=log_path,
            scenario_path=remote_scenario,
        )
        self.job_registry[job_id] = job
        await self._broadcast({"event": "gpu_workload_started", **job.to_dict()})
        job.completion_task = asyncio.create_task(
            self._auto_complete(job_id, job.started_at + job.duration_seconds)
        )
        return job

    async def _stop_single(self, job_id: str) -> None:
        job = self.job_registry.pop(job_id, None)
        if job is None:
            return
        if job.completion_task is not None:
            job.completion_task.cancel()
        with suppress(Exception):
            await self._ssh.run_command(job.machine_id, _stop_command(job))
        await self._broadcast(
            {
                "event": "gpu_workload_stopped",
                "job_id": job_id,
                "id": job.machine_id,
                "exit_code": 0,
            }
        )

    async def _auto_complete(self, job_id: str, expected_end: float) -> None:
        await asyncio.sleep(max(0.0, expected_end - time.time()))
        job = self.job_registry.pop(job_id, None)
        if job is not None:
            await self._broadcast(
                {
                    "event": "gpu_workload_stopped",
                    "job_id": job_id,
                    "id": job.machine_id,
                    "exit_code": 0,
                }
            )

    def _resolve_stop_job_ids(
        self,
        machine_ids: list[str] | Literal["all"] | None,
        job_ids: list[str] | Literal["all"] | None,
    ) -> list[str]:
        if job_ids == "all" or machine_ids == "all":
            return list(self.job_registry)
        ids: set[str] = set()
        if job_ids:
            ids.update(job_id for job_id in job_ids if job_id in self.job_registry)
        if machine_ids:
            machine_set = set(machine_ids)
            ids.update(
                job.job_id
                for job in self.job_registry.values()
                if job.machine_id in machine_set
            )
        return list(ids)

    async def _stream_setup_lines(self, machine_id: str, stream) -> None:
        async for line in stream:
            await self._log_setup(machine_id, line.rstrip())

    async def _log_setup(self, machine_id: str, line: str) -> None:
        if line:
            await self._broadcast(
                {"event": "gpu_workload_setup_log", "id": machine_id, "line": line}
            )

    async def _set_setup_progress(self, machine_id: str, step: str, status: str) -> None:
        item = self._setup_status.get(machine_id)
        if item is not None:
            item.step = step
            item.status = status
        await self._broadcast(
            {
                "event": "gpu_workload_setup_progress",
                "id": machine_id,
                "step": step,
                "status": status,
            }
        )

    async def _finish_setup(
        self,
        machine_id: str,
        exit_code: int,
        message: str | None = None,
    ) -> None:
        status = "success" if exit_code == 0 else "failed"
        item = self._setup_status.get(machine_id)
        if item is not None:
            item.status = status
            item.step = status
            item.exit_code = exit_code
            item.message = message
        await self._broadcast(
            {
                "event": "gpu_workload_setup_done",
                "id": machine_id,
                "status": status,
                "exit_code": exit_code,
                "message": message,
            }
        )


def _load_named_scenario(name: str) -> GpuScenario:
    return load_scenario(_scenario_path(name))


def _scenario_path(name: str) -> Path:
    if not NAME_RE.match(name):
        raise GpuWorkloadError("GPU scenario name may only contain letters, numbers, '_' and '-'")
    path = GPU_SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        raise GpuWorkloadError(f"unknown GPU scenario: {name}")
    return path


def _setup_command(image: str, gpu_index: int, no_cache: bool) -> str:
    no_cache_arg = "--no-cache " if no_cache else ""
    return "\n".join(
        [
            "set -euo pipefail",
            'echo "[gpu-setup] checking docker"',
            "command -v docker >/dev/null",
            'echo "[gpu-setup] checking nvidia-smi"',
            "command -v nvidia-smi >/dev/null",
            "nvidia-smi -L",
            'echo "[gpu-setup] building GPU workload image"',
            f"{DOCKER} build {no_cache_arg}-t {shlex.quote(image)} -f docker/gpu-workloads/Dockerfile .",
            'echo "[gpu-setup] verifying CUDA inside workload image"',
            (
                f"{DOCKER} run --rm --gpus {_docker_gpu_shell_arg(gpu_index)} {shlex.quote(image)} "
                "python3 -c 'import torch; print(\"torch cuda\", torch.cuda.is_available()); assert torch.cuda.is_available()'"
            ),
            'echo "[gpu-setup] GPU workload image ready"',
        ]
    )


def _stop_command(job: GpuWorkloadJobInfo) -> str:
    script = "\n".join(
        [
            f"{DOCKER} stop -t 5 {shlex.quote(job.container_name)} 2>/dev/null || true",
            f"{DOCKER} kill {shlex.quote(job.container_name)} 2>/dev/null || true",
            f"kill {job.pid} 2>/dev/null || true",
            f"rm -f {shlex.quote(job.scenario_path)}",
        ]
    )
    return f"bash -lc {shlex.quote(script)}"


def _docker_gpu_shell_arg(gpu_index: int) -> str:
    if gpu_index < 0:
        raise GpuWorkloadError("gpu_index must be non-negative")
    return shlex.quote(f'"device={gpu_index}"')


def _parse_pid(stdout: str) -> int:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    match = re.search(r"\b([0-9]+)\b", stdout)
    if not match:
        raise GpuWorkloadError(f"failed to parse GPU workload pid from stdout: {stdout!r}")
    return int(match.group(1))
