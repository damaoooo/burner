#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "UI" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from burn_controller import BurnError, MachineBurnRequest  # noqa: E402
from slurm_controller import SlurmConflictError, SlurmController, SlurmError  # noqa: E402
from waveform_store import WaveformStore  # noqa: E402


DEFAULT_READY_TIMEOUT_SECONDS = 1800
DEFAULT_READY_INTERVAL_SECONDS = 5.0
DEFAULT_INTERACTIVE_TIME_LIMIT = "00:15:00"
DEFAULT_INTERACTIVE_DURATION = "10m"
DEFAULT_INTERACTIVE_PERIOD = "1s"
DEFAULT_INTERACTIVE_WAVEFORM = "full"
DEFAULT_INTERACTIVE_TICK_SECONDS = 0.1
DEFAULT_INTERACTIVE_POLL_MS = 100
DEFAULT_INTERACTIVE_SAMPLE_MS = 200

T = TypeVar("T")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control Shaheen SLURM burner runs without the Web UI."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print command result as JSON",
    )
    subparsers = parser.add_subparsers(dest="command")

    interactive = subparsers.add_parser("interactive", help="open an interactive CLI shell")
    interactive.set_defaults(command="interactive")

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

    parser.set_defaults(command="interactive")

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
        if args.command == "interactive":
            return await interactive_shell(ctl)

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


async def interactive_shell(ctl: SlurmController) -> int:
    print_interactive_help()
    while True:
        try:
            command = normalize_interactive_command(input("burner> "))
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue

        if not command:
            continue
        if command == "quit":
            return 0
        if command == "help":
            print_interactive_help()
            continue

        try:
            if command == "status":
                await interactive_status(ctl)
            elif command == "submit":
                await interactive_submit(ctl)
            elif command == "wait-ready":
                await interactive_wait_ready(ctl)
            elif command == "start":
                await interactive_start(ctl)
            elif command == "run":
                await interactive_run(ctl)
            elif command == "stop":
                await interactive_stop(ctl)
            elif command == "release":
                await interactive_release(ctl)
            elif command == "export-load":
                await interactive_export_load(ctl)
            else:
                print(f"unknown command: {command}. Type help for commands.")
        except KeyboardInterrupt:
            print("\ncommand cancelled")
        except (BurnError, SlurmConflictError, SlurmError, TimeoutError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)


def normalize_interactive_command(command: str) -> str:
    command = command.strip().lower()
    aliases = {
        "?": "help",
        "h": "help",
        "1": "status",
        "2": "submit",
        "3": "wait-ready",
        "4": "start",
        "5": "run",
        "6": "stop",
        "7": "release",
        "8": "export-load",
        "q": "quit",
        "exit": "quit",
    }
    return aliases.get(command, command)


def print_interactive_help() -> None:
    print(
        "\n".join(
            [
                "Shaheen burner interactive CLI",
                "Commands:",
                "  1 status       show aggregate SLURM allocation state",
                "  2 submit       submit an allocation",
                "  3 wait-ready   wait until all workers are ready",
                "  4 start        start CPU burn on the current allocation",
                "  5 run          submit, wait-ready, then start",
                "  6 stop         stop burn and keep nodes allocated",
                "  7 release      release the current allocation",
                "  8 export-load  write latest load samples to CSV",
                "  help           show this menu",
                "  quit           exit the CLI",
                "",
            ]
        )
    )


async def interactive_status(ctl: SlurmController) -> None:
    status = await ctl.allocation_status()
    print(summarize_result(status))


async def interactive_submit(ctl: SlurmController) -> dict[str, object]:
    args = prompt_allocation_args()
    result = await ctl.submit_allocation(
        nodes=args.nodes,
        time_limit=args.time_limit,
        poll_ms=args.poll_ms,
        sample_ms=args.sample_ms,
    )
    print_result(non_json_args(), result)
    return result


async def interactive_wait_ready(ctl: SlurmController) -> dict[str, object]:
    timeout = prompt_float("Ready timeout seconds", DEFAULT_READY_TIMEOUT_SECONDS)
    interval = prompt_float("Ready poll interval seconds", DEFAULT_READY_INTERVAL_SECONDS)
    result = await wait_until_ready(
        ctl,
        timeout_seconds=timeout,
        interval_seconds=interval,
        quiet=False,
    )
    print_result(non_json_args(), result)
    return result


async def interactive_start(ctl: SlurmController) -> dict[str, object]:
    args = prompt_start_args()
    result = await start_all_ready(ctl, args)
    print_result(non_json_args(), result)
    return result


async def interactive_run(ctl: SlurmController) -> dict[str, object]:
    allocation_args = prompt_allocation_args()
    timeout = prompt_float("Ready timeout seconds", DEFAULT_READY_TIMEOUT_SECONDS)
    interval = prompt_float("Ready poll interval seconds", DEFAULT_READY_INTERVAL_SECONDS)
    start_args = prompt_start_args()

    allocation = await ctl.submit_allocation(
        nodes=allocation_args.nodes,
        time_limit=allocation_args.time_limit,
        poll_ms=allocation_args.poll_ms,
        sample_ms=allocation_args.sample_ms,
    )
    print_human(non_json_args(), f"submitted job {allocation.get('job_id')} for {allocation.get('nodes_requested')} nodes")
    ready = await wait_until_ready(
        ctl,
        timeout_seconds=timeout,
        interval_seconds=interval,
        quiet=False,
    )
    started = await start_all_ready(ctl, start_args)
    result = {"allocation": allocation, "ready": ready, "started": started}
    print_result(non_json_args(), result)
    return result


async def interactive_stop(ctl: SlurmController) -> None:
    await ctl.stop_burn(job_ids="all")
    print_result(non_json_args(), {"status": "stopped"})


async def interactive_release(ctl: SlurmController) -> dict[str, object] | None:
    if not prompt_bool("Release current allocation", default=False):
        print("release cancelled")
        return None
    result = await ctl.release_allocation()
    print_result(non_json_args(), result)
    return result


async def interactive_export_load(ctl: SlurmController) -> dict[str, object]:
    filename, content = ctl.export_load_csv()
    default = str(Path(filename).resolve())
    output = Path(prompt_text("Output CSV path", default))
    output.write_text(content, encoding="utf-8")
    result = {"output": str(output), "bytes": len(content.encode("utf-8"))}
    print_result(non_json_args(), result)
    return result


def prompt_allocation_args() -> argparse.Namespace:
    return argparse.Namespace(
        nodes=prompt_int("Nodes", required=True),
        time_limit=prompt_text("SLURM time limit", DEFAULT_INTERACTIVE_TIME_LIMIT),
        poll_ms=prompt_int("Worker poll ms", DEFAULT_INTERACTIVE_POLL_MS),
        sample_ms=prompt_int("Sample ms", DEFAULT_INTERACTIVE_SAMPLE_MS),
    )


def prompt_start_args() -> argparse.Namespace:
    start_at = prompt_text("Scheduled UTC start time, blank for immediate", "")
    return argparse.Namespace(
        duration=prompt_text("Burn duration", DEFAULT_INTERACTIVE_DURATION),
        period=prompt_text("Waveform period", DEFAULT_INTERACTIVE_PERIOD),
        waveform=prompt_text("Waveform", DEFAULT_INTERACTIVE_WAVEFORM),
        tick=prompt_float("Burner tick seconds", DEFAULT_INTERACTIVE_TICK_SECONDS),
        start_at=start_at or None,
    )


def prompt_text(label: str, default: str | None = None, required: bool = False) -> str:
    while True:
        raw = input(prompt_label(label, default))
        value = raw.strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print(f"{label} is required")


def prompt_int(label: str, default: int | None = None, required: bool = False) -> int:
    return prompt_cast(label, int, default, required)


def prompt_float(label: str, default: float | None = None, required: bool = False) -> float:
    return prompt_cast(label, float, default, required)


def prompt_cast(
    label: str,
    cast: Callable[[str], T],
    default: T | None = None,
    required: bool = False,
) -> T:
    while True:
        raw = input(prompt_label(label, default))
        value = raw.strip()
        if not value:
            if default is not None:
                return default
            if not required:
                raise ValueError(f"{label} is required")
            print(f"{label} is required")
            continue
        try:
            return cast(value)
        except ValueError:
            print(f"{label} must be a valid {cast.__name__}")


def prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("enter y or n")


def prompt_label(label: str, default: object | None = None) -> str:
    if default is None or default == "":
        return f"{label}: "
    return f"{label} [{default}]: "


def non_json_args() -> argparse.Namespace:
    return argparse.Namespace(json=False)


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
