from __future__ import annotations

import asyncio
import json
import math
import random
import re
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from config import REPO_ROOT, UI_ROOT
from remote_shell import conda_env_path_command


SCENARIO_DIR = UI_ROOT / "scenarios"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
WORKLOAD_TYPES: tuple[str, ...] = ("crypto", "compress", "compile", "python-cpu")
SOURCE_FILES: tuple[str, ...] = (
    "workloads/__init__.py",
    "workloads/runner.py",
)
SETUP_PACKAGES = (
    "build-essential",
    "openssl",
    "pigz",
    "xz-utils",
    "make",
    "coreutils",
    "python3",
)

Broadcast = Callable[[dict[str, object]], Awaitable[None]]


class WorkloadError(RuntimeError):
    pass


class WorkloadConflictError(WorkloadError):
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


class BurnLike(Protocol):
    def has_jobs(self, machine_id: str) -> bool: ...


@dataclass(frozen=True)
class WorkloadScenarioJob:
    machine_id: str
    workload: str
    delay_seconds: float
    duration_seconds: float
    workers: int

    def to_dict(self) -> dict[str, object]:
        return {
            "machine_id": self.machine_id,
            "workload": self.workload,
            "delay_seconds": self.delay_seconds,
            "duration_seconds": self.duration_seconds,
            "workers": self.workers,
        }


@dataclass(frozen=True)
class WorkloadScenario:
    name: str
    seed: int
    total_window_seconds: float
    jobs: list[WorkloadScenarioJob]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "seed": self.seed,
            "total_window_seconds": self.total_window_seconds,
            "jobs": [job.to_dict() for job in self.jobs],
        }


@dataclass
class WorkloadJobInfo:
    job_id: str
    scenario_name: str
    machine_id: str
    pid: int
    started_at: float
    duration_seconds: float
    delay_seconds: float
    workload: str
    workers: int
    log_path: str
    completion_task: asyncio.Task[None] | None = None

    def to_dict(self) -> dict[str, object]:
        elapsed = max(0.0, time.time() - self.started_at)
        return {
            "job_id": self.job_id,
            "scenario_name": self.scenario_name,
            "machine_id": self.machine_id,
            "pid": self.pid,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "elapsed_seconds": elapsed,
            "delay_seconds": self.delay_seconds,
            "workload": self.workload,
            "workers": self.workers,
            "log_path": self.log_path,
        }


@dataclass
class WorkloadSetupStatus:
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


class WorkloadController:
    def __init__(
        self,
        config: ConfigLike,
        ssh: SSHLike,
        file_transfer: FileTransferLike,
        burn: BurnLike,
        broadcast: Broadcast,
    ):
        self._config = config
        self._ssh = ssh
        self._file_transfer = file_transfer
        self._burn = burn
        self._broadcast = broadcast
        self.job_registry: dict[str, WorkloadJobInfo] = {}
        self._setup_running = False
        self._setup_status: dict[str, WorkloadSetupStatus] = {}
        self._setup_lock = asyncio.Lock()

    def list_scenarios(self) -> list[dict[str, object]]:
        SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
        records = []
        for path in sorted(SCENARIO_DIR.glob("*.json")):
            try:
                scenario = load_scenario(path.stem)
            except WorkloadError:
                continue
            records.append(
                {
                    "name": scenario.name,
                    "seed": scenario.seed,
                    "total_window_seconds": scenario.total_window_seconds,
                    "jobs": len(scenario.jobs),
                }
            )
        return records

    def get_scenario(self, name: str) -> WorkloadScenario:
        return load_scenario(name)

    def generate_scenario(
        self,
        name: str = "server-room",
        machine_ids: list[str] | None = None,
        seed: int = 20260618,
        total_window_seconds: float = 1800.0,
        min_duration_seconds: float = 300.0,
        max_duration_seconds: float = 1200.0,
        min_workers: int = 1,
        max_workers: int = 4,
    ) -> WorkloadScenario:
        _validate_name(name)
        if total_window_seconds <= 0:
            raise WorkloadError("total_window_seconds must be greater than 0")
        if min_duration_seconds <= 0 or max_duration_seconds < min_duration_seconds:
            raise WorkloadError("duration range is invalid")
        if min_workers <= 0 or max_workers < min_workers:
            raise WorkloadError("worker range is invalid")

        machines = self._config.list_machines()
        configured = {machine.id for machine in machines}
        if machine_ids is None:
            machine_ids = [machine.id for machine in machines]
        if not machine_ids:
            raise WorkloadError("at least one machine is required")
        for machine_id in machine_ids:
            if machine_id not in configured:
                raise WorkloadError(f"unknown machine id: {machine_id}")

        rng = random.Random(seed)
        jobs: list[WorkloadScenarioJob] = []
        workload_cycle = list(WORKLOAD_TYPES)
        for index, machine_id in enumerate(machine_ids):
            duration = rng.uniform(min_duration_seconds, max_duration_seconds)
            duration = min(duration, total_window_seconds)
            latest_delay = max(0.0, total_window_seconds - duration)
            delay = rng.uniform(0.0, latest_delay)
            workload = workload_cycle[index % len(workload_cycle)]
            if rng.random() < 0.35:
                workload = rng.choice(workload_cycle)
            jobs.append(
                WorkloadScenarioJob(
                    machine_id=machine_id,
                    workload=workload,
                    delay_seconds=round(delay, 3),
                    duration_seconds=round(duration, 3),
                    workers=rng.randint(min_workers, max_workers),
                )
            )

        scenario = WorkloadScenario(
            name=name,
            seed=seed,
            total_window_seconds=total_window_seconds,
            jobs=jobs,
        )
        save_scenario(scenario)
        return scenario

    async def reserve_setup(self, machine_ids: list[str] | None = None) -> list[str]:
        if machine_ids is None:
            machine_ids = [
                machine.id
                for machine in self._config.list_machines()
                if self._ssh.status_for(machine.id) == "connected"
            ]
        if not machine_ids:
            raise WorkloadError("at least one connected machine is required")

        async with self._setup_lock:
            if self._setup_running:
                raise WorkloadConflictError("Workload setup is already running")
            seen: set[str] = set()
            for machine_id in machine_ids:
                if machine_id in seen:
                    raise WorkloadError(f"duplicate machine id: {machine_id}")
                seen.add(machine_id)
                self._config.get_machine(machine_id)
                if self._ssh.status_for(machine_id) != "connected":
                    raise WorkloadError(f"machine {machine_id} is not connected")
                if self._burn.has_jobs(machine_id) or self.has_jobs(machine_id):
                    raise WorkloadConflictError(f"machine {machine_id} is currently running a job")

            self._setup_running = True
            self._setup_status = {
                machine_id: WorkloadSetupStatus(machine_id)
                for machine_id in machine_ids
            }
            return machine_ids

    async def run_reserved_setup(self, machine_ids: list[str]) -> None:
        exit_code = 0
        try:
            results = await asyncio.gather(
                *(self._run_setup_machine(machine_id) for machine_id in machine_ids),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception) or result != 0:
                    exit_code = 1
        finally:
            self._setup_running = False
            await self._broadcast(
                {"event": "workload_setup_complete", "exit_code": exit_code}
            )

    async def start_scenario(self, scenario: WorkloadScenario) -> list[WorkloadJobInfo]:
        if self._setup_running:
            raise WorkloadConflictError("Workload setup is currently running")
        if not scenario.jobs:
            raise WorkloadError("scenario must contain at least one job")

        for job in scenario.jobs:
            self._config.get_machine(job.machine_id)
            if self._ssh.status_for(job.machine_id) != "connected":
                raise WorkloadError(f"machine {job.machine_id} is not connected")
            if self._burn.has_jobs(job.machine_id):
                raise WorkloadConflictError(f"machine {job.machine_id} is currently burning")
            _validate_job(job)

        self._check_overlaps(scenario)
        machine_ids = sorted({job.machine_id for job in scenario.jobs})
        await asyncio.gather(*(self._sync_sources(machine_id) for machine_id in machine_ids))
        started = await asyncio.gather(*(self._start_single(scenario, job) for job in scenario.jobs))
        return list(started)

    async def stop_workloads(
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

    async def _run_setup_machine(self, machine_id: str) -> int:
        try:
            await self._sync_sources(machine_id)
            await self._run_setup_command(machine_id)
            await self._finish_setup(machine_id, exit_code=0)
            return 0
        except Exception as exc:
            await self._log_setup(machine_id, f"workload setup failed: {exc}")
            await self._finish_setup(machine_id, exit_code=1, message=str(exc))
            return 1

    async def _sync_sources(self, machine_id: str) -> None:
        machine = self._config.get_machine(machine_id)
        remote_dirs = sorted({str(Path(machine.workdir) / Path(path).parent) for path in SOURCE_FILES})
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(path) for path in remote_dirs)
        stdout, stderr, exit_code = await self._ssh.run_command(machine_id, mkdir_cmd)
        if exit_code != 0:
            raise WorkloadError(f"sync mkdir failed with exit code {exit_code}: {stderr.strip()}")
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

    async def _run_setup_command(self, machine_id: str) -> None:
        machine = self._config.get_machine(machine_id)
        await self._set_setup_progress(machine_id, "install", "running")
        command = _setup_command()
        full_cmd = f"bash -lc {shlex.quote(f'cd {shlex.quote(machine.workdir)} && {command}')}"
        conn = self._ssh.get_connection(machine_id)
        async with conn.create_process(full_cmd) as process:
            await asyncio.gather(
                self._stream_setup_lines(machine_id, process.stdout),
                self._stream_setup_lines(machine_id, process.stderr),
            )
            exit_status = process.exit_status
        if exit_status != 0:
            raise WorkloadError(f"setup failed with exit code {exit_status}")

    async def _start_single(
        self,
        scenario: WorkloadScenario,
        plan: WorkloadScenarioJob,
    ) -> WorkloadJobInfo:
        machine = self._config.get_machine(plan.machine_id)
        job_id = f"{plan.machine_id}-{uuid.uuid4().hex[:12]}"
        log_path = f"/tmp/burner_workload_{job_id}.log"
        started_at = time.time() + plan.delay_seconds
        runner_args = [
            "-m",
            "workloads.runner",
            "run-job",
            "--job-id",
            job_id,
            "--workload",
            plan.workload,
            "--workers",
            str(plan.workers),
            "--duration-seconds",
            format_float(plan.duration_seconds),
            "--seed",
            str(scenario.seed),
        ]
        runner_script = "\n".join(
            [
                f"sleep {format_float(plan.delay_seconds)}",
                f"export PYTHONPATH={shlex.quote(machine.workdir)}:${{PYTHONPATH:-}}",
                'if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3; else PYTHON_BIN=python; fi',
                f'exec "$PYTHON_BIN" {shlex.join(runner_args)}',
            ]
        )
        inner = (
            f"cd {shlex.quote(machine.workdir)} || exit 1; "
            f"nohup setsid bash -lc {shlex.quote(runner_script)} "
            f"> {shlex.quote(log_path)} 2>&1 < /dev/null & echo $!"
        )
        full_cmd = conda_env_path_command(machine.conda_env, inner)
        stdout, stderr, exit_code = await self._ssh.run_command(plan.machine_id, full_cmd)
        if exit_code != 0:
            raise WorkloadError(f"{plan.machine_id}: failed to start workload: {stderr.strip()}")
        pid = _parse_pid(stdout)
        job = WorkloadJobInfo(
            job_id=job_id,
            scenario_name=scenario.name,
            machine_id=plan.machine_id,
            pid=pid,
            started_at=started_at,
            duration_seconds=plan.duration_seconds,
            delay_seconds=plan.delay_seconds,
            workload=plan.workload,
            workers=plan.workers,
            log_path=log_path,
        )
        self.job_registry[job_id] = job
        await self._broadcast({"event": "workload_started", **job.to_dict()})
        job.completion_task = asyncio.create_task(
            self._auto_complete(job_id, started_at + plan.duration_seconds)
        )
        return job

    async def _stop_single(self, job_id: str) -> None:
        job = self.job_registry.pop(job_id, None)
        if job is None:
            return
        if job.completion_task is not None:
            job.completion_task.cancel()
        command = _stop_command(job.pid)
        with suppress(Exception):
            await self._ssh.run_command(job.machine_id, command)
        await self._broadcast(
            {
                "event": "workload_stopped",
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
                    "event": "workload_stopped",
                    "job_id": job_id,
                    "id": job.machine_id,
                    "exit_code": 0,
                }
            )

    def _check_overlaps(self, scenario: WorkloadScenario) -> None:
        conflicts = []
        now = time.time()
        for plan in scenario.jobs:
            new_start = now + plan.delay_seconds
            new_end = new_start + plan.duration_seconds
            for existing in self.job_registry.values():
                if existing.machine_id != plan.machine_id:
                    continue
                existing_start = existing.started_at
                existing_end = existing.started_at + existing.duration_seconds
                if _windows_overlap(new_start, new_end, existing_start, existing_end):
                    conflicts.append(f"{plan.machine_id}: requested workload overlaps {existing.job_id}")
        if conflicts:
            raise WorkloadConflictError("; ".join(conflicts))

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
                {"event": "workload_setup_log", "id": machine_id, "line": line}
            )

    async def _set_setup_progress(self, machine_id: str, step: str, status: str) -> None:
        item = self._setup_status.get(machine_id)
        if item is not None:
            item.step = step
            item.status = status
        await self._broadcast(
            {
                "event": "workload_setup_progress",
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
                "event": "workload_setup_done",
                "id": machine_id,
                "status": status,
                "exit_code": exit_code,
                "message": message,
            }
        )


def load_scenario(name: str) -> WorkloadScenario:
    _validate_name(name)
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        raise WorkloadError(f"unknown workload scenario: {name}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkloadError(f"invalid scenario JSON: {exc}") from exc
    return parse_scenario(raw)


def save_scenario(scenario: WorkloadScenario) -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    _validate_name(scenario.name)
    path = SCENARIO_DIR / f"{scenario.name}.json"
    path.write_text(
        json.dumps(scenario.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_scenario(raw: object) -> WorkloadScenario:
    if not isinstance(raw, dict):
        raise WorkloadError("scenario must be an object")
    name = _string(raw, "name")
    _validate_name(name)
    seed = int(raw.get("seed", 0))
    total_window_seconds = _positive_float(raw.get("total_window_seconds", 0), "total_window_seconds")
    jobs_raw = raw.get("jobs")
    if not isinstance(jobs_raw, list) or not jobs_raw:
        raise WorkloadError("scenario jobs must be a non-empty list")
    jobs = [parse_scenario_job(item, index) for index, item in enumerate(jobs_raw)]
    return WorkloadScenario(name, seed, total_window_seconds, jobs)


def parse_scenario_job(raw: object, index: int = 0) -> WorkloadScenarioJob:
    if not isinstance(raw, dict):
        raise WorkloadError(f"job #{index + 1} must be an object")
    job = WorkloadScenarioJob(
        machine_id=_string(raw, "machine_id"),
        workload=_string(raw, "workload"),
        delay_seconds=_non_negative_float(raw.get("delay_seconds", 0), "delay_seconds"),
        duration_seconds=_positive_float(raw.get("duration_seconds", 0), "duration_seconds"),
        workers=int(raw.get("workers", 0)),
    )
    _validate_job(job)
    return job


def _validate_job(job: WorkloadScenarioJob) -> None:
    if job.workload not in WORKLOAD_TYPES:
        raise WorkloadError(f"unknown workload: {job.workload}")
    if job.workers <= 0:
        raise WorkloadError("workers must be greater than 0")
    if job.duration_seconds <= 0:
        raise WorkloadError("duration_seconds must be greater than 0")
    if job.delay_seconds < 0:
        raise WorkloadError("delay_seconds must be non-negative")


def _setup_command() -> str:
    package_list = " ".join(shlex.quote(package) for package in SETUP_PACKAGES)
    return "\n".join(
        [
            "set -euo pipefail",
            'echo "[setup] checking workload dependencies"',
            "missing=0",
            'for cmd in gcc make openssl pigz xz python3; do',
            '  if ! command -v "$cmd" >/dev/null 2>&1; then',
            '    echo "[setup] missing $cmd"',
            "    missing=1",
            "  fi",
            "done",
            "if [ \"$missing\" -eq 1 ]; then",
            "  if ! command -v apt-get >/dev/null 2>&1; then",
            '    echo "[setup] apt-get not found" >&2',
            "    exit 1",
            "  fi",
            '  echo "[setup] installing packages with sudo apt"',
            "  sudo -n apt-get update",
            f"  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y {package_list}",
            "fi",
            'echo "[setup] workload dependencies ready"',
        ]
    )


def _stop_command(pid: int) -> str:
    script = "\n".join(
        [
            f"pid={pid}",
            'if kill -0 "$pid" 2>/dev/null; then',
            '  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true',
            "  for _ in 1 2 3 4 5 6 7 8 9 10; do",
            '    kill -0 "$pid" 2>/dev/null || exit 0',
            "    sleep 0.5",
            "  done",
            '  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true',
            "fi",
        ]
    )
    return f"bash -lc {shlex.quote(script)}"


def format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _parse_pid(stdout: str) -> int:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    match = re.search(r"\b([0-9]+)\b", stdout)
    if not match:
        raise WorkloadError(f"failed to parse workload pid from stdout: {stdout!r}")
    return int(match.group(1))


def _windows_overlap(
    new_start: float,
    new_end: float,
    existing_start: float,
    existing_end: float,
) -> bool:
    grace = 5.0
    return new_start < existing_end + grace and existing_start < new_end + grace


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise WorkloadError("scenario name may only contain letters, numbers, '_' and '-'")


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkloadError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_float(value: object, key: str) -> float:
    number = _float(value, key)
    if number <= 0:
        raise WorkloadError(f"{key} must be greater than 0")
    return number


def _non_negative_float(value: object, key: str) -> float:
    number = _float(value, key)
    if number < 0:
        raise WorkloadError(f"{key} must be non-negative")
    return number


def _float(value: object, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkloadError(f"{key} must be a number") from exc
    if not math.isfinite(number):
        raise WorkloadError(f"{key} must be finite")
    return number
