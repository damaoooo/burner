from __future__ import annotations

import asyncio
import math
import re
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from config import ConfigStore
from file_transfer import FileTransfer
from remote_shell import conda_run_command
from ssh_manager import SSHManager
from waveform_store import WaveformStore


_DURATION_RE = re.compile(r"^([1-9][0-9]*)([smh])$")
_PERIOD_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?|\.[0-9]+)([smh])$")

Broadcast = Callable[[dict[str, object]], Awaitable[None]]


class BurnError(RuntimeError):
    pass


class BurnOverlapError(BurnError):
    pass


@dataclass(frozen=True)
class MachineBurnRequest:
    id: str
    enabled: bool
    burn_cpu: bool
    burn_gpu: bool
    delay_seconds: float
    waveform_name: str


@dataclass(frozen=True)
class PlannedJob:
    job_id: str
    target: MachineBurnRequest
    waveform_path: str
    started_at: float
    start_time_utc: datetime | None
    duration_seconds: float
    sync_mode: str


@dataclass
class JobInfo:
    job_id: str
    machine_id: str
    pid: int
    started_at: float
    duration_seconds: float
    burn_cpu: bool
    burn_gpu: bool
    delay_seconds: float
    waveform_name: str
    waveform_path: str
    sync_mode: str
    completion_task: asyncio.Task[None] | None = None

    def to_dict(self) -> dict[str, object]:
        elapsed = max(0.0, time.time() - self.started_at)
        return {
            "job_id": self.job_id,
            "machine_id": self.machine_id,
            "pid": self.pid,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "elapsed_seconds": elapsed,
            "burn_cpu": self.burn_cpu,
            "burn_gpu": self.burn_gpu,
            "delay_seconds": self.delay_seconds,
            "waveform_name": self.waveform_name,
            "sync_mode": self.sync_mode,
        }


class BurnController:
    def __init__(
        self,
        config: ConfigStore,
        ssh: SSHManager,
        file_transfer: FileTransfer,
        waveforms: WaveformStore,
        broadcast: Broadcast,
    ):
        self._config = config
        self._ssh = ssh
        self._file_transfer = file_transfer
        self._waveforms = waveforms
        self._broadcast = broadcast
        self.job_registry: dict[str, JobInfo] = {}

    async def start_burn(
        self,
        sync_mode: Literal["immediate", "delayed", "scheduled"],
        duration: str,
        period: str,
        machines: list[MachineBurnRequest],
        start_time_utc: str | None = None,
        tick_seconds: float = 0.1,
    ) -> list[JobInfo]:
        targets = [machine for machine in machines if machine.enabled]
        if not targets:
            raise BurnError("at least one machine must be enabled")
        if sync_mode not in ("immediate", "delayed", "scheduled"):
            raise BurnError("sync_mode must be immediate, delayed, or scheduled")

        duration_seconds = parse_duration(duration)
        parse_period(period)
        parse_tick(tick_seconds)

        for target in targets:
            self._config.get_machine(target.id)
            if self._ssh.status_for(target.id) != "connected":
                raise BurnError(f"machine {target.id} is not connected")
            if not target.burn_cpu and not target.burn_gpu:
                raise BurnError(f"machine {target.id} must burn CPU or GPU")
            self._waveforms.get_waveform(target.waveform_name)

        base_time: datetime | None = None
        if sync_mode == "delayed":
            base_time = datetime.now(UTC) + timedelta(seconds=5)
        elif sync_mode == "scheduled":
            base_time = parse_utc_start(start_time_utc)
            if base_time <= datetime.now(UTC):
                raise BurnError("scheduled start time must be in the future")

        plans = self._build_plans(sync_mode, targets, duration_seconds, base_time)
        self._check_overlaps(plans)
        await asyncio.gather(*(self._copy_waveform(plan) for plan in plans))

        jobs = await asyncio.gather(
            *[self._start_single(plan, duration, period, tick_seconds) for plan in plans]
        )
        return list(jobs)

    async def stop_burn(
        self,
        machine_ids: list[str] | Literal["all"] | None = None,
        job_ids: list[str] | Literal["all"] | None = None,
    ) -> None:
        ids = self._resolve_stop_job_ids(machine_ids, job_ids)
        await asyncio.gather(*(self._stop_single(job_id) for job_id in ids))

    def status(self) -> list[dict[str, object]]:
        return [job.to_dict() for job in self.job_registry.values()]

    def has_jobs(self, machine_id: str) -> bool:
        return any(job.machine_id == machine_id for job in self.job_registry.values())

    def _build_plans(
        self,
        sync_mode: str,
        targets: list[MachineBurnRequest],
        duration_seconds: float,
        base_time: datetime | None,
    ) -> list[PlannedJob]:
        plans: list[PlannedJob] = []
        now = datetime.now(UTC)
        for target in targets:
            job_id = f"{target.id}-{uuid.uuid4().hex[:12]}"
            machine = self._config.get_machine(target.id)
            start_time_utc = None
            if base_time is not None:
                start_time_utc = base_time + timedelta(seconds=target.delay_seconds)
                started_at = start_time_utc.timestamp()
            else:
                started_at = now.timestamp()
            plans.append(
                PlannedJob(
                    job_id=job_id,
                    target=target,
                    waveform_path=f"{machine.workdir.rstrip('/')}/ui_waveforms/{job_id}.csv",
                    started_at=started_at,
                    start_time_utc=start_time_utc,
                    duration_seconds=duration_seconds,
                    sync_mode=sync_mode,
                )
            )
        return plans

    def _check_overlaps(self, plans: list[PlannedJob]) -> None:
        conflicts = []
        for plan in plans:
            new_start = plan.started_at
            new_end = new_start + plan.duration_seconds
            for existing in self.job_registry.values():
                if existing.machine_id != plan.target.id:
                    continue
                existing_start = existing.started_at
                existing_end = existing_start + existing.duration_seconds
                if _windows_overlap(new_start, new_end, existing_start, existing_end):
                    conflicts.append(
                        f"{plan.target.id}: {format_epoch(new_start)}-{format_epoch(new_end)} "
                        f"overlaps job {existing.job_id} "
                        f"{format_epoch(existing_start)}-{format_epoch(existing_end)}"
                    )
        if conflicts:
            raise BurnOverlapError("; ".join(conflicts))

    async def _copy_waveform(self, plan: PlannedJob) -> None:
        machine = self._config.get_machine(plan.target.id)
        await self._ssh.run_command(
            plan.target.id,
            f"mkdir -p {shlex.quote(machine.workdir.rstrip('/') + '/ui_waveforms')}",
        )
        await self._file_transfer.scp_to_remote(
            plan.target.id,
            self._waveforms.path_for(plan.target.waveform_name),
            plan.waveform_path,
        )

    async def _start_single(
        self,
        plan: PlannedJob,
        duration: str,
        period: str,
        tick_seconds: float,
    ) -> JobInfo:
        target = plan.target
        machine = self._config.get_machine(target.id)

        args = ["./burner"]
        if target.burn_cpu:
            args.append("--cpu")
        if target.burn_gpu:
            args.append("--gpu")
        relative_waveform = f"./ui_waveforms/{plan.job_id}.csv"
        args.extend(["-f", relative_waveform, "-t", duration, "-p", period])
        args.extend(["--tick", format_float(tick_seconds)])
        if plan.start_time_utc is not None:
            args.extend(["--start", iso_z(plan.start_time_utc)])

        burner_cmd = shlex.join(args)
        log_path = f"/tmp/burner_{plan.job_id}.log"
        inner = (
            f"cd {shlex.quote(machine.workdir)} || exit 1; "
            f"nohup {burner_cmd} > {shlex.quote(log_path)} 2>&1 & echo $!"
        )
        full_cmd = conda_run_command(machine.conda_env, inner)
        stdout, stderr, exit_code = await self._ssh.run_command(target.id, full_cmd)
        if exit_code != 0:
            raise BurnError(f"{target.id}: failed to start burner: {stderr.strip()}")

        pid = _parse_pid(stdout)
        started_at = plan.started_at if plan.start_time_utc is not None else time.time()
        job = JobInfo(
            job_id=plan.job_id,
            machine_id=target.id,
            pid=pid,
            started_at=started_at,
            duration_seconds=plan.duration_seconds,
            burn_cpu=target.burn_cpu,
            burn_gpu=target.burn_gpu,
            delay_seconds=target.delay_seconds if plan.start_time_utc is not None else 0.0,
            waveform_name=target.waveform_name,
            waveform_path=plan.waveform_path,
            sync_mode=plan.sync_mode,
        )
        self.job_registry[plan.job_id] = job

        await self._broadcast(
            {
                "event": "burn_started",
                "job_id": plan.job_id,
                "id": target.id,
                "pid": pid,
                "started_at": started_at,
                "duration_seconds": plan.duration_seconds,
                "burn_cpu": target.burn_cpu,
                "burn_gpu": target.burn_gpu,
                "delay_seconds": job.delay_seconds,
                "waveform_name": target.waveform_name,
                "sync_mode": plan.sync_mode,
            }
        )
        job.completion_task = asyncio.create_task(
            self._auto_complete(plan.job_id, started_at + plan.duration_seconds)
        )
        return job

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

    async def _stop_single(self, job_id: str) -> None:
        job = self.job_registry.pop(job_id, None)
        if job is None:
            return
        if job.completion_task is not None:
            job.completion_task.cancel()
        try:
            await self._ssh.run_command(
                job.machine_id,
                f"kill {job.pid} 2>/dev/null || true; rm -f {shlex.quote(job.waveform_path)}",
            )
        except Exception:
            pass
        await self._broadcast(
            {
                "event": "burn_stopped",
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
                    "event": "burn_stopped",
                    "job_id": job_id,
                    "id": job.machine_id,
                    "exit_code": 0,
                }
            )


def parse_duration(value: str) -> float:
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise BurnError("duration must match INTEGER[s|m|h]")
    amount = int(match.group(1))
    return float(amount * {"s": 1, "m": 60, "h": 3600}[match.group(2)])


def parse_period(value: str) -> float:
    match = _PERIOD_RE.match(value.strip())
    if not match:
        raise BurnError("period must match positive NUMBER[s|m|h]")
    amount = float(match.group(1))
    if amount <= 0:
        raise BurnError("period must be greater than 0")
    return amount * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


def parse_tick(value: float) -> float:
    if not math.isfinite(value) or value < 0.01 or value > 1.0:
        raise BurnError("tick_seconds must be between 0.01 and 1.0")
    return value


def format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc_start(value: str | None) -> datetime:
    if not value:
        raise BurnError("start_time_utc is required for scheduled mode")
    if not value.endswith("Z"):
        raise BurnError("start_time_utc must be a UTC ISO timestamp ending with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BurnError("start_time_utc must use ISO format like 2026-05-12T10:00:00Z") from exc
    return parsed.astimezone(UTC)


def _parse_pid(stdout: str) -> int:
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    match = re.search(r"\b([0-9]+)\b", stdout)
    if not match:
        raise BurnError(f"failed to parse burner pid from stdout: {stdout!r}")
    return int(match.group(1))


def _windows_overlap(
    new_start: float,
    new_end: float,
    existing_start: float,
    existing_end: float,
) -> bool:
    grace = 5.0
    return new_start < existing_end + grace and existing_start < new_end + grace


def format_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
