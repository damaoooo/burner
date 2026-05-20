from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from burn_controller import (
    BurnError,
    BurnOverlapError,
    MachineBurnRequest,
    format_float,
    iso_z,
    parse_duration,
    parse_period,
    parse_tick,
    parse_utc_start,
)
from config import REPO_ROOT
from waveform_store import WaveformStore


UTC = timezone.utc
DEFAULT_CONTROL_BASE = Path("/scratch/zhoul0e/burner-slurm-control")
DEFAULT_CONDA_ENV = "burner"
DEFAULT_START_LEAD_SECONDS = 2.0
WORKER_STALE_SECONDS = 30.0
LOAD_EXPORT_COLUMNS = [
    "session_id",
    "job_id",
    "node_id",
    "timestamp",
    "cpu_watts",
    "cpu_watts_estimated",
    "cpu_utilization_percent",
    "cpu_freq_mhz_avg",
    "cpu_freq_mhz_min",
    "cpu_freq_mhz_max",
    "loadavg_1m",
]

_TIME_LIMIT_RE = re.compile(
    r"^([1-9][0-9]*|[0-9]+:[0-5]?[0-9](?::[0-5]?[0-9])?|[0-9]+-[0-9]+:[0-5]?[0-9](?::[0-5]?[0-9])?)$"
)

Broadcast = Callable[[dict[str, object]], Awaitable[None]]
CommandRunner = Callable[[list[str]], Awaitable[tuple[str, str, int]]]


class SlurmError(RuntimeError):
    pass


class SlurmConflictError(SlurmError):
    pass


class SlurmClient:
    async def submit_batch(
        self,
        script_path: Path,
        nodes: int,
        time_limit: str,
        job_name: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> str:
        raise NotImplementedError

    async def job_state(self, job_id: str) -> str:
        raise NotImplementedError

    async def cancel(self, job_id: str) -> None:
        raise NotImplementedError


class PySlurmClient(SlurmClient):
    def __init__(self):
        import pyslurm

        self._pyslurm = pyslurm

    async def submit_batch(
        self,
        script_path: Path,
        nodes: int,
        time_limit: str,
        job_name: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> str:
        def submit() -> str:
            desc = self._pyslurm.JobSubmitDescription(
                name=job_name,
                nodes=nodes,
                time_limit=time_limit,
                resource_sharing="exclusive",
                standard_output=str(stdout_path),
                standard_error=str(stderr_path),
                script=str(script_path),
            )
            return str(desc.submit())

        return await asyncio.to_thread(submit)

    async def job_state(self, job_id: str) -> str:
        def load_state() -> str:
            job = self._pyslurm.Job.load(int(job_id))
            return str(job.state)

        try:
            return await asyncio.to_thread(load_state)
        except Exception:
            return "UNKNOWN"

    async def cancel(self, job_id: str) -> None:
        def cancel() -> None:
            self._pyslurm.Job(int(job_id)).cancel()

        await asyncio.to_thread(cancel)


class CliSlurmClient(SlurmClient):
    def __init__(self, runner: CommandRunner | None = None):
        self._runner = runner or _run_command

    async def submit_batch(
        self,
        script_path: Path,
        nodes: int,
        time_limit: str,
        job_name: str,
        stdout_path: Path,
        stderr_path: Path,
    ) -> str:
        del nodes, time_limit, job_name, stdout_path, stderr_path
        stdout, stderr, exit_code = await self._runner(["sbatch", str(script_path)])
        if exit_code != 0:
            raise SlurmError(stderr.strip() or stdout.strip() or "sbatch failed")
        return parse_sbatch_job_id(stdout)

    async def job_state(self, job_id: str) -> str:
        stdout, stderr, exit_code = await self._runner(["squeue", "-h", "-j", job_id, "-o", "%T"])
        if exit_code != 0:
            return f"SLURM_ERROR: {(stderr or stdout).strip()}"
        state = stdout.strip().splitlines()
        if state:
            return state[0].strip() or "UNKNOWN"
        return "UNKNOWN"

    async def cancel(self, job_id: str) -> None:
        stdout, stderr, exit_code = await self._runner(["scancel", job_id])
        if exit_code != 0:
            raise SlurmError(stderr.strip() or stdout.strip() or "scancel failed")


@dataclass(frozen=True)
class SlurmSession:
    session_id: str
    job_id: str
    session_dir: Path
    nodes_requested: int
    time_limit: str
    poll_ms: int
    sample_ms: int
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "job_id": self.job_id,
            "session_dir": str(self.session_dir),
            "nodes_requested": self.nodes_requested,
            "time_limit": self.time_limit,
            "poll_ms": self.poll_ms,
            "sample_ms": self.sample_ms,
            "created_at": self.created_at,
        }


@dataclass
class SlurmBurnJob:
    job_id: str
    machine_id: str
    started_at: float
    duration_seconds: float
    waveform_name: str
    sync_mode: str

    def to_dict(self) -> dict[str, object]:
        elapsed = max(0.0, time.time() - self.started_at)
        return {
            "job_id": self.job_id,
            "machine_id": self.machine_id,
            "pid": 0,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "elapsed_seconds": elapsed,
            "burn_cpu": True,
            "burn_gpu": False,
            "delay_seconds": 0.0,
            "waveform_name": self.waveform_name,
            "sync_mode": self.sync_mode,
        }


@dataclass(frozen=True)
class LoadSample:
    timestamp_ms: int
    timestamp: str
    watts: float
    cpu_watts: float | None
    cpu_watts_estimated: float | None
    cpu_utilization_percent: float | None
    cpu_freq_mhz_avg: float | None
    cpu_freq_mhz_min: float | None
    cpu_freq_mhz_max: float | None
    loadavg_1m: float | None


class SlurmController:
    def __init__(
        self,
        waveforms: WaveformStore,
        broadcast: Broadcast,
        control_base: Path | None = None,
        repo_root: Path = REPO_ROOT,
        conda_env: str | None = None,
        runner: CommandRunner | None = None,
        slurm_client: SlurmClient | None = None,
    ):
        self._waveforms = waveforms
        self._broadcast = broadcast
        self._control_base = Path(
            control_base
            or os.environ.get("BURNER_SLURM_CONTROL_DIR", str(DEFAULT_CONTROL_BASE))
        )
        self._repo_root = Path(
            os.environ.get("BURNER_REPO_ROOT", str(repo_root))
        ).resolve()
        self._conda_env = conda_env or os.environ.get("BURNER_CONDA_ENV", DEFAULT_CONDA_ENV)
        self._slurm = slurm_client or build_slurm_client(runner)
        self._jobs: dict[str, SlurmBurnJob] = {}
        self._lock = asyncio.Lock()

    async def submit_allocation(
        self,
        nodes: int,
        time_limit: str,
        poll_ms: int,
        sample_ms: int = 200,
    ) -> dict[str, object]:
        nodes = validate_node_count(nodes)
        time_limit = validate_time_limit(time_limit)
        poll_ms = validate_poll_ms(poll_ms)
        sample_ms = validate_sample_ms(sample_ms)

        async with self._lock:
            current = self._load_current_session()
            if current is not None:
                state = await self._slurm_state(current.job_id)
                if state not in {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "BOOT_FAIL", "UNKNOWN"}:
                    raise SlurmConflictError(
                        f"SLURM allocation {current.job_id} is still active ({state})"
                    )
            self._cleanup_old_sessions()

            session_id = f"shaheen-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
            session_dir = self._control_base / session_id
            for relative in ("nodes", "samples", "logs"):
                (session_dir / relative).mkdir(parents=True, exist_ok=True)

            script_path = session_dir / "submit.sbatch"
            script_path.write_text(
                render_sbatch_script(
                    nodes=nodes,
                    time_limit=time_limit,
                    session_id=session_id,
                    session_dir=session_dir,
                    repo_root=self._repo_root,
                    conda_env=self._conda_env,
                    poll_ms=poll_ms,
                    sample_ms=sample_ms,
                ),
                encoding="utf-8",
            )
            script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

            job_id = await self._slurm.submit_batch(
                script_path=script_path,
                nodes=nodes,
                time_limit=time_limit,
                job_name=f"burner-{session_id[:24]}",
                stdout_path=session_dir / "slurm-%j.out",
                stderr_path=session_dir / "slurm-%j.err",
            )

            session = SlurmSession(
                session_id=session_id,
                job_id=job_id,
                session_dir=session_dir,
                nodes_requested=nodes,
                time_limit=time_limit,
                poll_ms=poll_ms,
                sample_ms=sample_ms,
                created_at=time.time(),
            )
            self._write_session(session)
            self._write_current_session(session)
            await self._broadcast(
                {
                    "event": "allocation_changed",
                    **(await self.allocation_status()),
                }
            )
            return await self.allocation_status()

    async def allocation_status(self) -> dict[str, object]:
        session = self._load_current_session()
        if session is None:
            return {
                "active": False,
                "status": "none",
                "nodes": [],
            }

        slurm_state = await self._slurm_state(session.job_id)
        nodes = self._read_nodes(session)
        ready_count = len(
            [
                node
                for node in nodes
                if node.get("connection_status") == "connected"
                and node.get("worker_status") in {"ready", "arming", "burning"}
            ]
        )
        return {
            "active": slurm_state not in {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "BOOT_FAIL", "UNKNOWN"},
            "status": slurm_state,
            "session_id": session.session_id,
            "job_id": session.job_id,
            "session_dir": str(session.session_dir),
            "nodes_requested": session.nodes_requested,
            "nodes_ready": ready_count,
            "poll_ms": session.poll_ms,
            "sample_ms": session.sample_ms,
            "time_limit": session.time_limit,
            "created_at": session.created_at,
            "nodes": nodes,
        }

    async def list_machines(self) -> list[dict[str, object]]:
        session = self._load_current_session()
        if session is None:
            return []
        return self._read_nodes(session)

    async def start_burn(
        self,
        sync_mode: Literal["immediate", "delayed", "scheduled"],
        duration: str,
        period: str,
        machines: list[MachineBurnRequest],
        start_time_utc: str | None = None,
        tick_seconds: float = 0.1,
    ) -> list[SlurmBurnJob]:
        if sync_mode == "delayed":
            raise BurnError("Shaheen SLURM mode supports immediate and scheduled starts only")
        if sync_mode not in {"immediate", "scheduled"}:
            raise BurnError("sync_mode must be immediate or scheduled")

        duration_seconds = parse_duration(duration)
        parse_period(period)
        parse_tick(tick_seconds)

        session = self._require_session()
        nodes = self._ready_nodes(session)
        if len(nodes) != session.nodes_requested:
            raise BurnError(
                f"waiting for all workers: {len(nodes)}/{session.nodes_requested} ready"
            )

        enabled = [machine for machine in machines if machine.enabled]
        ready_ids = {str(node["id"]) for node in nodes}
        enabled_ids = {machine.id for machine in enabled}
        if enabled_ids != ready_ids:
            raise BurnError("all ready SLURM nodes must be enabled for synchronized burn")
        if any(not machine.burn_cpu for machine in enabled):
            raise BurnError("Shaheen SLURM mode requires CPU burn on every node")
        if any(machine.burn_gpu for machine in enabled):
            raise BurnError("GPU burn is disabled on Shaheen")

        waveform_names = {machine.waveform_name for machine in enabled}
        if len(waveform_names) != 1:
            raise BurnError("Shaheen SLURM mode requires one shared waveform for all nodes")
        waveform_name = next(iter(waveform_names))
        waveform_source = self._waveforms.path_for(waveform_name)
        waveform_path = session.session_dir / "curve.csv"
        shutil.copyfile(waveform_source, waveform_path)

        if sync_mode == "scheduled":
            start_time = parse_utc_start(start_time_utc)
            min_start = datetime.now(UTC) + timedelta(seconds=self._start_lead_seconds(session))
            if start_time <= min_start:
                raise BurnError(
                    f"scheduled start time must be at least {self._start_lead_seconds(session):.1f}s in the future"
                )
        else:
            start_time = datetime.now(UTC) + timedelta(seconds=self._start_lead_seconds(session))

        plans = [
            SlurmBurnJob(
                job_id=f"{session.session_id}-{node['id']}-{uuid.uuid4().hex[:8]}",
                machine_id=str(node["id"]),
                started_at=start_time.timestamp(),
                duration_seconds=duration_seconds,
                waveform_name=waveform_name,
                sync_mode=sync_mode,
            )
            for node in nodes
        ]
        self._check_overlaps(plans)
        sequence = self._next_command_sequence(session)
        self._write_command(
            session,
            {
                "sequence": sequence,
                "action": "start",
                "created_at": iso_z(datetime.now(UTC)),
                "start_at": iso_z(start_time),
                "duration": duration,
                "period": period,
                "tick_seconds": tick_seconds,
                "waveform_path": str(waveform_path),
                "waveform_name": waveform_name,
            },
        )

        for job in plans:
            self._jobs[job.job_id] = job
            await self._broadcast({"event": "burn_started", "id": job.machine_id, **job.to_dict()})
        return plans

    async def stop_burn(
        self,
        machine_ids: list[str] | Literal["all"] | None = None,
        job_ids: list[str] | Literal["all"] | None = None,
    ) -> None:
        del machine_ids, job_ids
        session = self._load_current_session()
        ids = list(self._jobs)
        if session is not None:
            self._write_command(
                session,
                {
                    "sequence": self._next_command_sequence(session),
                    "action": "stop",
                    "created_at": iso_z(datetime.now(UTC)),
                },
            )
        for job_id in ids:
            job = self._jobs.pop(job_id, None)
            if job is not None:
                await self._broadcast(
                    {
                        "event": "burn_stopped",
                        "job_id": job.job_id,
                        "id": job.machine_id,
                        "exit_code": 0,
                    }
                )

    async def release_allocation(self) -> dict[str, object]:
        session = self._load_current_session()
        if session is None:
            return await self.allocation_status()
        await self.stop_burn(job_ids="all")
        self._write_command(
            session,
            {
                "sequence": self._next_command_sequence(session),
                "action": "release",
                "created_at": iso_z(datetime.now(UTC)),
            },
        )
        await self._slurm.cancel(session.job_id)
        self._clear_current_session()
        payload = await self.allocation_status()
        await self._broadcast({"event": "allocation_changed", **payload})
        return payload

    def export_load_csv(self) -> tuple[str, str]:
        session = self._load_current_session() or self._latest_session()
        if session is None:
            raise SlurmError("no SLURM session samples are available")

        sample_paths = sorted((session.session_dir / "samples").glob("*.csv"))
        if not sample_paths:
            raise SlurmError("no node sample CSV files are available for the latest SLURM session")

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=LOAD_EXPORT_COLUMNS)
        writer.writeheader()
        rows = 0
        for sample_path in sample_paths:
            node_id = sample_path.stem
            try:
                with sample_path.open("r", newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        if not row.get("timestamp"):
                            continue
                        writer.writerow(
                            {
                                "session_id": session.session_id,
                                "job_id": session.job_id,
                                "node_id": node_id,
                                "timestamp": row.get("timestamp", ""),
                                "cpu_watts": row.get("cpu_watts", ""),
                                "cpu_watts_estimated": row.get("cpu_watts_estimated", ""),
                                "cpu_utilization_percent": row.get("cpu_utilization_percent", ""),
                                "cpu_freq_mhz_avg": row.get("cpu_freq_mhz_avg", ""),
                                "cpu_freq_mhz_min": row.get("cpu_freq_mhz_min", ""),
                                "cpu_freq_mhz_max": row.get("cpu_freq_mhz_max", ""),
                                "loadavg_1m": row.get("loadavg_1m", ""),
                            }
                        )
                        rows += 1
            except OSError:
                continue
        if rows == 0:
            raise SlurmError("no node load samples are available for the latest SLURM session")
        return f"{session.session_id}-load.csv", output.getvalue()

    def load_series(self, max_points: int = 1200, include_nodes: bool = False) -> dict[str, object]:
        session = self._load_current_session() or self._latest_session()
        if session is None:
            raise SlurmError("no SLURM session samples are available")

        max_points = max(10, min(int(max_points), 5000))
        samples_by_node = self._read_load_samples(session)
        if not samples_by_node:
            raise SlurmError("no node load samples are available for the latest SLURM session")

        nodes = []
        if include_nodes:
            for node_id, samples in sorted(samples_by_node.items()):
                nodes.append(
                    {
                        "node_id": node_id,
                        "sample_count": len(samples),
                        "points": [
                            _load_sample_to_dict(sample)
                            for sample in _downsample_load_samples(samples, max_points)
                        ],
                    }
                )

        return {
            "session_id": session.session_id,
            "job_id": session.job_id,
            "generated_at": iso_z(datetime.now(UTC)),
            "node_count": len(samples_by_node),
            "nodes": nodes,
            "cluster": {
                "sample_count": sum(len(samples) for samples in samples_by_node.values()),
                "points": _cluster_load_points(samples_by_node, max_points),
            },
        }

    def status(self) -> list[dict[str, object]]:
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if now >= job.started_at + job.duration_seconds
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        return [job.to_dict() for job in self._jobs.values()]

    def has_jobs(self) -> bool:
        self.status()
        return bool(self._jobs)

    def _ready_nodes(self, session: SlurmSession) -> list[dict[str, object]]:
        return [
            node
            for node in self._read_nodes(session)
            if node.get("connection_status") == "connected"
            and node.get("worker_status") == "ready"
        ]

    def _read_nodes(self, session: SlurmSession) -> list[dict[str, object]]:
        nodes = []
        for path in sorted((session.session_dir / "nodes").glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            node_id = str(raw.get("node_id") or path.stem)
            hw = raw.get("hw_info") if isinstance(raw.get("hw_info"), dict) else {}
            latest_power = raw.get("latest_power") if isinstance(raw.get("latest_power"), dict) else None
            heartbeat = _parse_iso_epoch(raw.get("heartbeat_at"))
            stale = heartbeat is None or time.time() - heartbeat > WORKER_STALE_SECONDS
            worker_status = str(raw.get("status") or "unknown")
            connection_status = "connected"
            if stale:
                connection_status = "error"
            elif worker_status in {"initializing", "building"}:
                connection_status = "connecting"
            error_message = "worker heartbeat is stale" if stale else raw.get("message")
            job = next(
                (
                    job.to_dict()
                    for job in self._jobs.values()
                    if job.machine_id == node_id
                ),
                None,
            )
            cpu_socket_count = int(hw.get("cpu_socket_count") or 0)
            cpu_tdp_per_socket = _float_or_zero(hw.get("cpu_tdp_per_socket_watts") or hw.get("cpu_tdp_watts"))
            cpu_tdp_total = _float_or_zero(hw.get("cpu_tdp_total_watts"))
            if cpu_tdp_total <= 0 and cpu_socket_count > 0 and cpu_tdp_per_socket > 0:
                cpu_tdp_total = cpu_socket_count * cpu_tdp_per_socket
            cpu_tdp = cpu_tdp_total or cpu_tdp_per_socket
            nodes.append(
                {
                    "id": node_id,
                    "name": node_id,
                    "host": str(hw.get("ip_address") or raw.get("hostname") or node_id),
                    "port": 0,
                    "username": "",
                    "identity_file": "",
                    "workdir": str(self._repo_root),
                    "cpu_tdp": cpu_tdp,
                    "gpu_tdp": 0.0,
                    "conda_env": self._conda_env,
                    "connection_status": connection_status,
                    "error_message": error_message,
                    "worker_status": worker_status,
                    "hw_info": {
                        "cpu_model": str(hw.get("cpu_model") or ""),
                        "cpu_tdp": cpu_tdp,
                        "cpu_socket_count": cpu_socket_count,
                        "cpu_tdp_per_socket_watts": cpu_tdp_per_socket,
                        "cpu_tdp_total_watts": cpu_tdp_total,
                        "gpu_tdp": 0.0,
                        "gpus": [],
                        "cpu_count": int(hw.get("cpu_count") or 0),
                        "memory_total_gb": _float_or_zero(hw.get("memory_total_gb")),
                        "ip_address": str(hw.get("ip_address") or ""),
                        "slurm_node": str(raw.get("slurm_node") or node_id),
                        "worker_status": worker_status,
                        "last_heartbeat": raw.get("heartbeat_at"),
                        "latest_power": latest_power,
                    },
                    "job": job,
                }
            )
        return nodes

    def _read_load_samples(self, session: SlurmSession) -> dict[str, list[LoadSample]]:
        samples_by_node: dict[str, list[LoadSample]] = {}
        for sample_path in sorted((session.session_dir / "samples").glob("*.csv")):
            samples = _read_load_sample_file(sample_path)
            if samples:
                samples_by_node[sample_path.stem] = samples
        return samples_by_node

    def _check_overlaps(self, plans: list[SlurmBurnJob]) -> None:
        conflicts = []
        for plan in plans:
            new_start = plan.started_at
            new_end = new_start + plan.duration_seconds
            for existing in self._jobs.values():
                if existing.machine_id != plan.machine_id:
                    continue
                existing_start = existing.started_at
                existing_end = existing_start + existing.duration_seconds
                if new_start < existing_end + 5.0 and existing_start < new_end + 5.0:
                    conflicts.append(
                        f"{plan.machine_id}: requested window overlaps {existing.job_id}"
                    )
        if conflicts:
            raise BurnOverlapError("; ".join(conflicts))

    def _start_lead_seconds(self, session: SlurmSession) -> float:
        return max(DEFAULT_START_LEAD_SECONDS, (session.poll_ms / 1000.0) * 5.0)

    async def _slurm_state(self, job_id: str) -> str:
        return await self._slurm.job_state(job_id)

    def _require_session(self) -> SlurmSession:
        session = self._load_current_session()
        if session is None:
            raise BurnError("no active SLURM allocation")
        return session

    @property
    def _current_path(self) -> Path:
        return self._control_base / "current_session.json"

    def _load_current_session(self) -> SlurmSession | None:
        try:
            raw = json.loads(self._current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return _session_from_dict(raw)

    def _latest_session(self) -> SlurmSession | None:
        if not self._control_base.exists():
            return None
        candidates: list[tuple[float, SlurmSession]] = []
        for session_path in self._control_base.glob("shaheen-*/session.json"):
            try:
                raw = json.loads(session_path.read_text(encoding="utf-8"))
                mtime = session_path.stat().st_mtime
            except (OSError, json.JSONDecodeError):
                continue
            session = _session_from_dict(raw)
            if session is not None:
                candidates.append((mtime, session))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _write_current_session(self, session: SlurmSession) -> None:
        self._control_base.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._current_path, session.to_dict())

    def _clear_current_session(self) -> None:
        try:
            self._current_path.unlink()
        except FileNotFoundError:
            pass

    def _write_session(self, session: SlurmSession) -> None:
        _atomic_write_json(session.session_dir / "session.json", session.to_dict())

    def _cleanup_old_sessions(self) -> None:
        self._control_base.mkdir(parents=True, exist_ok=True)
        for path in self._control_base.iterdir():
            if path == self._current_path:
                continue
            if not (
                path.name.startswith("shaheen-")
                or path.name.startswith("diag-")
                or path.name.startswith("freq-diag-")
            ):
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                with suppress(OSError):
                    path.unlink()

    def _next_command_sequence(self, session: SlurmSession) -> int:
        path = session.session_dir / "command.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return int(raw.get("sequence") or 0) + 1
        except (OSError, json.JSONDecodeError, ValueError):
            return 1

    def _write_command(self, session: SlurmSession, payload: dict[str, object]) -> None:
        _atomic_write_json(session.session_dir / "command.json", payload)


def validate_node_count(value: int) -> int:
    if not isinstance(value, int) or value < 1:
        raise SlurmError("nodes must be a positive integer")
    return value


def validate_poll_ms(value: int) -> int:
    if not isinstance(value, int) or value < 10 or value > 1000:
        raise SlurmError("poll_ms must be between 10 and 1000")
    return value


def validate_sample_ms(value: int) -> int:
    if not isinstance(value, int) or value < 30 or value > 10000:
        raise SlurmError("sample_ms must be between 30 and 10000")
    return value


def validate_time_limit(value: str) -> str:
    value = value.strip()
    if not _TIME_LIMIT_RE.match(value):
        raise SlurmError("time_limit must be a SLURM duration like 05:00:00")
    return value


def parse_sbatch_job_id(stdout: str) -> str:
    match = re.search(r"\bSubmitted batch job\s+([0-9]+)\b", stdout)
    if not match:
        raise SlurmError(f"failed to parse sbatch job id from: {stdout!r}")
    return match.group(1)


def build_slurm_client(runner: CommandRunner | None = None) -> SlurmClient:
    if runner is not None:
        return CliSlurmClient(runner)
    try:
        return PySlurmClient()
    except Exception:
        return CliSlurmClient()


def render_sbatch_script(
    nodes: int,
    time_limit: str,
    session_id: str,
    session_dir: Path,
    repo_root: Path,
    conda_env: str,
    poll_ms: int,
    sample_ms: int = 200,
) -> str:
    return f"""#!/usr/bin/env bash
#SBATCH -N {nodes}
#SBATCH --time={time_limit}
#SBATCH --exclusive
#SBATCH --job-name=burner-{session_id[:24]}
#SBATCH --output={session_dir}/slurm-%j.out
#SBATCH --error={session_dir}/slurm-%j.err

set -euo pipefail

export BURNER_SLURM_SESSION_DIR={session_dir}
export BURNER_REPO_ROOT={repo_root}
export BURNER_WORKER_POLL_MS={poll_ms}
export BURNER_WORKER_SAMPLE_MS={sample_ms}
export BURNER_WORKER_LOCAL_SAMPLE_DIR="${{BURNER_WORKER_LOCAL_SAMPLE_DIR:-/tmp}}"
export BURNER_CONDA_ENV={conda_env}
export BURNER_CONDA_ROOT="${{BURNER_CONDA_ROOT:-/scratch/${{USER}}/miniconda3}}"
export BURNER_ENV_PYTHON="${{BURNER_CONDA_ROOT}}/envs/${{BURNER_CONDA_ENV}}/bin/python"

cd "${{BURNER_REPO_ROOT}}"
if [[ -n "${{CONDA_EXE:-}}" ]]; then
  export PATH="$(dirname "$(dirname "${{CONDA_EXE}}")")/condabin:${{PATH}}"
fi
if [[ -f "${{BURNER_CONDA_ROOT}}/etc/profile.d/conda.sh" ]]; then
  source "${{BURNER_CONDA_ROOT}}/etc/profile.d/conda.sh"
else
  export PATH="${{BURNER_CONDA_ROOT}}/condabin:${{PATH}}"
fi
bash scripts/build_lookbusy.sh

if [[ -n "${{BURNER_WORKER_PYTHON:-}}" && -x "${{BURNER_WORKER_PYTHON}}" ]]; then
  WORKER_PYTHON="${{BURNER_WORKER_PYTHON}}"
elif [[ -x "${{BURNER_ENV_PYTHON}}" ]]; then
  WORKER_PYTHON="${{BURNER_ENV_PYTHON}}"
elif [[ -x "${{BURNER_CONDA_ROOT}}/bin/python3" ]]; then
  WORKER_PYTHON="${{BURNER_CONDA_ROOT}}/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  WORKER_PYTHON="$(command -v python3)"
else
  echo "No usable Python 3 found for SLURM worker" >&2
  exit 1
fi
echo "Using worker python: ${{WORKER_PYTHON}}"

srun --ntasks="${{SLURM_NNODES}}" --ntasks-per-node=1 \\
  "${{WORKER_PYTHON}}" UI/backend/slurm_worker.py
"""


async def _run_command(args: list[str]) -> tuple[str, str, int]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        process.returncode,
    )


def _read_load_sample_file(path: Path) -> list[LoadSample]:
    samples: list[LoadSample] = []
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                timestamp = row.get("timestamp", "")
                timestamp_ms = _parse_iso_ms(timestamp)
                if timestamp_ms is None:
                    continue
                cpu_watts = _parse_optional_float(row.get("cpu_watts"))
                cpu_watts_estimated = _parse_optional_float(row.get("cpu_watts_estimated"))
                watts = cpu_watts if cpu_watts is not None else cpu_watts_estimated
                if watts is None:
                    continue
                samples.append(
                    LoadSample(
                        timestamp_ms=timestamp_ms,
                        timestamp=timestamp,
                        watts=watts,
                        cpu_watts=cpu_watts,
                        cpu_watts_estimated=cpu_watts_estimated,
                        cpu_utilization_percent=_parse_optional_float(row.get("cpu_utilization_percent")),
                        cpu_freq_mhz_avg=_parse_optional_float(row.get("cpu_freq_mhz_avg")),
                        cpu_freq_mhz_min=_parse_optional_float(row.get("cpu_freq_mhz_min")),
                        cpu_freq_mhz_max=_parse_optional_float(row.get("cpu_freq_mhz_max")),
                        loadavg_1m=_parse_optional_float(row.get("loadavg_1m")),
                    )
                )
    except OSError:
        return []
    return sorted(samples, key=lambda sample: sample.timestamp_ms)


def _downsample_load_samples(samples: list[LoadSample], max_points: int) -> list[LoadSample]:
    if len(samples) <= max_points:
        return samples
    bucket_size = math.ceil(len(samples) / max_points)
    return [
        _average_load_samples(samples[index : index + bucket_size])
        for index in range(0, len(samples), bucket_size)
    ][:max_points]


def _average_load_samples(samples: list[LoadSample]) -> LoadSample:
    timestamp_ms = round(sum(sample.timestamp_ms for sample in samples) / len(samples))
    return LoadSample(
        timestamp_ms=timestamp_ms,
        timestamp=_iso_from_ms(timestamp_ms),
        watts=_mean_required(sample.watts for sample in samples),
        cpu_watts=_mean_optional(sample.cpu_watts for sample in samples),
        cpu_watts_estimated=_mean_optional(sample.cpu_watts_estimated for sample in samples),
        cpu_utilization_percent=_mean_optional(sample.cpu_utilization_percent for sample in samples),
        cpu_freq_mhz_avg=_mean_optional(sample.cpu_freq_mhz_avg for sample in samples),
        cpu_freq_mhz_min=_mean_optional(sample.cpu_freq_mhz_min for sample in samples),
        cpu_freq_mhz_max=_mean_optional(sample.cpu_freq_mhz_max for sample in samples),
        loadavg_1m=_mean_optional(sample.loadavg_1m for sample in samples),
    )


def _cluster_load_points(samples_by_node: dict[str, list[LoadSample]], max_points: int) -> list[dict[str, object]]:
    non_empty = [samples for samples in samples_by_node.values() if samples]
    if not non_empty:
        return []
    start_ms = min(samples[0].timestamp_ms for samples in non_empty)
    end_ms = max(samples[-1].timestamp_ms for samples in non_empty)
    if end_ms <= start_ms:
        return [
            {
                "timestamp": _iso_from_ms(start_ms),
                "watts": round(sum(samples[0].watts for samples in non_empty), 6),
                "nodes_reported": len(non_empty),
            }
        ]

    step_ms = max(50, math.ceil((end_ms - start_ms) / max(1, max_points - 1)))
    node_indexes = {node_id: 0 for node_id in samples_by_node}
    points = []
    current_ms = start_ms
    while current_ms <= end_ms:
        total = 0.0
        nodes_reported = 0
        for node_id, samples in samples_by_node.items():
            index = node_indexes[node_id]
            while index + 1 < len(samples) and samples[index + 1].timestamp_ms <= current_ms:
                index += 1
            node_indexes[node_id] = index
            if samples[0].timestamp_ms <= current_ms <= samples[-1].timestamp_ms:
                total += samples[index].watts
                nodes_reported += 1
        if nodes_reported:
            points.append(
                {
                    "timestamp": _iso_from_ms(current_ms),
                    "watts": round(total, 6),
                    "nodes_reported": nodes_reported,
                }
            )
        current_ms += step_ms

    if len(points) > max_points:
        return points[:max_points]
    return points


def _load_sample_to_dict(sample: LoadSample) -> dict[str, object]:
    return {
        "timestamp": sample.timestamp,
        "watts": round(sample.watts, 6),
        "cpu_watts": _round_optional(sample.cpu_watts),
        "cpu_watts_estimated": _round_optional(sample.cpu_watts_estimated),
        "cpu_utilization_percent": _round_optional(sample.cpu_utilization_percent),
        "cpu_freq_mhz_avg": _round_optional(sample.cpu_freq_mhz_avg),
        "cpu_freq_mhz_min": _round_optional(sample.cpu_freq_mhz_min),
        "cpu_freq_mhz_max": _round_optional(sample.cpu_freq_mhz_max),
        "loadavg_1m": _round_optional(sample.loadavg_1m),
    }


def _parse_optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_ms(value: str) -> int | None:
    if not value or not value.endswith("Z"):
        return None
    try:
        return round(datetime.fromisoformat(value[:-1] + "+00:00").timestamp() * 1000)
    except ValueError:
        return None


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mean_required(values) -> float:
    items = [float(value) for value in values]
    return round(sum(items) / len(items), 6)


def _mean_optional(values) -> float | None:
    items = [float(value) for value in values if value is not None]
    if not items:
        return None
    return round(sum(items) / len(items), 6)


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _session_from_dict(raw: dict[str, Any]) -> SlurmSession | None:
    try:
        return SlurmSession(
            session_id=str(raw["session_id"]),
            job_id=str(raw["job_id"]),
            session_dir=Path(str(raw["session_dir"])),
            nodes_requested=int(raw["nodes_requested"]),
            time_limit=str(raw["time_limit"]),
            poll_ms=int(raw["poll_ms"]),
            sample_ms=int(raw.get("sample_ms") or 200),
            created_at=float(raw.get("created_at") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _parse_iso_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").timestamp()
    except ValueError:
        return None


def _float_or_zero(value: object) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
