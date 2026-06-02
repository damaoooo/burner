#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "UI" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from burn_controller import BurnError, MachineBurnRequest  # noqa: E402
from slurm_controller import SlurmConflictError, SlurmController, SlurmError  # noqa: E402
from waveform_store import WaveformStore  # noqa: E402


DEFAULT_READY_TIMEOUT_SECONDS = 1800
DEFAULT_READY_INTERVAL_SECONDS = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control Shaheen SLURM burner runs without the Web UI."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print command result as JSON",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="submit a SLURM allocation")
    add_allocation_args(submit)

    wait_ready = subparsers.add_parser("wait-ready", help="wait until all workers are ready")
    wait_ready.add_argument("--timeout", type=float, default=DEFAULT_READY_TIMEOUT_SECONDS)
    wait_ready.add_argument("--interval", type=float, default=DEFAULT_READY_INTERVAL_SECONDS)

    start = subparsers.add_parser("start", help="start CPU burn on all ready nodes")
    add_start_args(start)

    run = subparsers.add_parser("run", help="submit, wait for ready workers, then start burn")
    add_allocation_args(run)
    add_start_args(run)
    run.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT_SECONDS)
    run.add_argument("--ready-interval", type=float, default=DEFAULT_READY_INTERVAL_SECONDS)

    stop = subparsers.add_parser("stop", help="stop the current burn but keep the allocation")
    stop.set_defaults(command="stop")

    release = subparsers.add_parser("release", help="release the current allocation")
    release.set_defaults(command="release")

    status = subparsers.add_parser("status", help="print current allocation status")
    status.add_argument("--nodes", action="store_true", help="include one page of node records")
    status.add_argument("--offset", type=int, default=0)
    status.add_argument("--limit", type=int, default=50)

    export = subparsers.add_parser("export-load", help="write combined node load CSV")
    export.add_argument("-o", "--output", type=Path, help="output CSV path")

    return parser


def add_allocation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-N", "--nodes", type=int, required=True, help="SLURM node count")
    parser.add_argument("--time", "--time-limit", dest="time_limit", required=True, help="SLURM time limit")
    parser.add_argument("--poll-ms", type=int, default=100, help="worker command polling interval")
    parser.add_argument("--sample-ms", type=int, default=200, help="UI/latest-power sample interval")


def add_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-t", "--duration", required=True, help="burn duration, for example 10m or 600s")
    parser.add_argument("-p", "--period", required=True, help="waveform period, for example 1s")
    parser.add_argument("-f", "--waveform", default="full", help="waveform name from tests/fixtures or UI waveforms")
    parser.add_argument("--tick", type=float, default=0.1, help="burner tick seconds")
    parser.add_argument(
        "--start-at",
        help="UTC scheduled start time, for example 2026-06-02T12:00:00Z. Omit for immediate synchronized start.",
    )


def controller() -> SlurmController:
    async def no_broadcast(payload: dict[str, object]) -> None:
        del payload

    return SlurmController(WaveformStore(), no_broadcast, repo_root=ROOT)


async def main_async(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctl = controller()
    try:
        if args.command == "submit":
            result = await ctl.submit_allocation(
                nodes=args.nodes,
                time_limit=args.time_limit,
                poll_ms=args.poll_ms,
                sample_ms=args.sample_ms,
            )
            print_result(args, result)
            return 0

        if args.command == "wait-ready":
            result = await wait_until_ready(
                ctl,
                timeout_seconds=args.timeout,
                interval_seconds=args.interval,
                quiet=args.json,
            )
            print_result(args, result)
            return 0

        if args.command == "start":
            result = await start_all_ready(ctl, args)
            print_result(args, result)
            return 0

        if args.command == "run":
            allocation = await ctl.submit_allocation(
                nodes=args.nodes,
                time_limit=args.time_limit,
                poll_ms=args.poll_ms,
                sample_ms=args.sample_ms,
            )
            print_human(args, f"submitted job {allocation.get('job_id')} for {allocation.get('nodes_requested')} nodes")
            ready = await wait_until_ready(
                ctl,
                timeout_seconds=args.ready_timeout,
                interval_seconds=args.ready_interval,
                quiet=args.json,
            )
            started = await start_all_ready(ctl, args)
            print_result(args, {"allocation": allocation, "ready": ready, "started": started})
            return 0

        if args.command == "stop":
            await ctl.stop_burn(job_ids="all")
            print_result(args, {"status": "stopped"})
            return 0

        if args.command == "release":
            result = await ctl.release_allocation()
            print_result(args, result)
            return 0

        if args.command == "status":
            result = await ctl.allocation_status()
            if args.nodes:
                result = {
                    **result,
                    "nodes": await ctl.list_machines(offset=args.offset, limit=args.limit),
                }
            print_result(args, result)
            return 0

        if args.command == "export-load":
            filename, content = ctl.export_load_csv()
            output = args.output or Path(filename)
            output.write_text(content, encoding="utf-8")
            print_result(args, {"output": str(output), "bytes": len(content.encode("utf-8"))})
            return 0

        raise AssertionError(f"unhandled command: {args.command}")
    except (BurnError, SlurmConflictError, SlurmError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


async def wait_until_ready(
    ctl: SlurmController,
    timeout_seconds: float,
    interval_seconds: float,
    quiet: bool = False,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while True:
        status = await ctl.allocation_status()
        last = status
        if not status.get("active"):
            raise SlurmError(f"allocation is not active ({status.get('status')})")
        ready = int(status.get("nodes_ready") or 0)
        requested = int(status.get("nodes_requested") or 0)
        if not quiet:
            print(
                f"ready {ready}/{requested} state={status.get('status')} job={status.get('job_id')}",
                flush=True,
            )
        if requested > 0 and ready >= requested:
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out waiting for ready workers: {ready}/{requested}; last state={status.get('status')}"
            )
        await asyncio.sleep(max(0.5, interval_seconds))


async def start_all_ready(ctl: SlurmController, args: argparse.Namespace) -> dict[str, object]:
    ready_nodes = await ctl.list_machines(offset=0, limit=None)
    requests = build_machine_requests(ready_nodes, args.waveform)
    jobs = await ctl.start_burn(
        "scheduled" if args.start_at else "immediate",
        args.duration,
        args.period,
        requests,
        args.start_at,
        args.tick,
    )
    started = [job.to_dict() for job in jobs]
    return {
        "jobs_started": len(started),
        "start_at": started[0]["started_at"] if started else None,
        "duration_seconds": started[0]["duration_seconds"] if started else None,
        "waveform": args.waveform,
        "sync_mode": "scheduled" if args.start_at else "immediate",
    }


def build_machine_requests(nodes: list[dict[str, object]], waveform_name: str) -> list[MachineBurnRequest]:
    return [
        MachineBurnRequest(
            id=str(node["id"]),
            enabled=True,
            burn_cpu=True,
            burn_gpu=False,
            delay_seconds=0.0,
            waveform_name=waveform_name,
        )
        for node in nodes
    ]


def print_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print_human(args, summarize_result(result))


def print_human(args: argparse.Namespace, message: str) -> None:
    if not args.json:
        print(message, flush=True)


def summarize_result(result: dict[str, Any]) -> str:
    if "allocation" in result and "started" in result:
        allocation = result["allocation"]
        started = result["started"]
        return (
            f"job {allocation.get('job_id')} ready; started {started.get('jobs_started')} nodes "
            f"at {started.get('start_at')}"
        )
    if "jobs_started" in result:
        return f"started {result['jobs_started']} nodes at {result.get('start_at')}"
    if "output" in result:
        return f"wrote {result['output']} ({result.get('bytes')} bytes)"
    if "active" in result:
        requested = result.get("nodes_requested", 0)
        ready = result.get("nodes_ready", 0)
        job = result.get("job_id", "none")
        return f"state={result.get('status')} active={result.get('active')} job={job} ready={ready}/{requested}"
    return json.dumps(result, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
