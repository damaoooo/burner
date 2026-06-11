from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import click
import typer
from rich.console import Console
from rich.table import Table

from .burn import CpuBurnController
from .daemon import ChokerDaemon, ChokerStrategy
from .monitor import CpuLoadMonitor
from .runtime import (
    DEFAULT_LOG_FILE,
    DEFAULT_PID_FILE,
    PROJECT_ROOT,
    PidFile,
    PidStatus,
    read_pid_status,
    remove_pid_file,
    terminate_process,
)


DEFAULT_THRESHOLD = 10.0
DEFAULT_TARGET = 100.0
DEFAULT_WINDOW_MS = 1000
DEFAULT_STARTUP_TIMEOUT = 3.0
DEFAULT_STOP_TIMEOUT = 5.0

app = typer.Typer(no_args_is_help=True, help="Idle CPU load daemon for burner.")
console = Console()


@app.command()
def start(
    strategy: ChokerStrategy = typer.Option(ChokerStrategy.COMPLEMENT, help="CPU fill strategy."),
    target: float = typer.Option(DEFAULT_TARGET, help="Target CPU percent for complement strategy."),
    threshold: float = typer.Option(DEFAULT_THRESHOLD, help="External CPU threshold percent."),
    window_ms: int = typer.Option(DEFAULT_WINDOW_MS, help="CPU sampling window in milliseconds."),
    pid_file: Path = typer.Option(DEFAULT_PID_FILE, help="Pidfile path."),
    log_file: Path = typer.Option(DEFAULT_LOG_FILE, help="Log file path."),
    startup_timeout: float = typer.Option(DEFAULT_STARTUP_TIMEOUT, hidden=True),
) -> None:
    """Start choker as a background daemon."""
    _validate_config(threshold, target, window_ms)
    pid_file = Path(pid_file)
    log_file = Path(log_file)

    status = read_pid_status(pid_file, require_choker=True)
    if status.state == PidStatus.RUNNING:
        console.print(f"choker running with pid {status.pid}")
        return
    if status.state in {PidStatus.STALE, PidStatus.INVALID}:
        remove_pid_file(pid_file)

    command = [
        sys.executable,
        "-m",
        "choker",
        "run",
        "--strategy",
        strategy.value,
        "--target",
        str(target),
        "--threshold",
        str(threshold),
        "--window-ms",
        str(window_ms),
        "--pid-file",
        str(pid_file),
        "--log-file",
        str(log_file),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = _prepend_pythonpath(PROJECT_ROOT, env.get("PYTHONPATH"))
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        status = read_pid_status(pid_file, require_choker=True)
        if status.state == PidStatus.RUNNING:
            console.print(f"choker started with pid {status.pid}")
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)

    raise click.ClickException(
        f"choker failed to start; check log file at {log_file}"
    )


@app.command()
def stop(
    pid_file: Path = typer.Option(DEFAULT_PID_FILE, help="Pidfile path."),
    timeout: float = typer.Option(DEFAULT_STOP_TIMEOUT, help="Seconds to wait for shutdown."),
) -> None:
    """Stop the background choker daemon."""
    status = read_pid_status(pid_file, require_choker=True)
    if status.state == PidStatus.STOPPED:
        console.print("choker stopped")
        return
    if status.state in {PidStatus.STALE, PidStatus.INVALID}:
        remove_pid_file(pid_file)
        console.print("choker stopped; removed stale pidfile")
        return
    assert status.pid is not None
    if not terminate_process(status.pid, timeout):
        raise click.ClickException(f"failed to stop choker pid {status.pid}")
    remove_pid_file(pid_file)
    console.print("choker stopped")


@app.command()
def status(
    pid_file: Path = typer.Option(DEFAULT_PID_FILE, help="Pidfile path."),
) -> None:
    """Show daemon status."""
    state = read_pid_status(pid_file, require_choker=True)
    table = Table(show_header=False, box=None)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("status", state.state.value)
    table.add_row("pidfile", str(state.path))
    if state.pid is not None:
        table.add_row("pid", str(state.pid))
    if state.message:
        table.add_row("message", state.message)
    console.print(table)


@app.command("run")
def run_command(
    strategy: ChokerStrategy = typer.Option(ChokerStrategy.COMPLEMENT, help="CPU fill strategy."),
    target: float = typer.Option(DEFAULT_TARGET, help="Target CPU percent for complement strategy."),
    threshold: float = typer.Option(DEFAULT_THRESHOLD, help="External CPU threshold percent."),
    window_ms: int = typer.Option(DEFAULT_WINDOW_MS, help="CPU sampling window in milliseconds."),
    pid_file: Path = typer.Option(DEFAULT_PID_FILE, help="Pidfile path."),
    log_file: Path = typer.Option(DEFAULT_LOG_FILE, help="Log file path."),
    max_iterations: int | None = typer.Option(None, hidden=True),
) -> None:
    """Run choker in the foreground."""
    _validate_config(threshold, target, window_ms)
    if max_iterations is not None and max_iterations <= 0:
        raise click.BadParameter("max-iterations must be greater than 0")

    _configure_logging(log_file)
    logger = logging.getLogger("choker")
    pid_file = Path(pid_file)
    log_file = Path(log_file)
    logger.info(
        "starting choker strategy=%s target=%.2f threshold=%.2f window_ms=%d pid_file=%s log_file=%s",
        strategy.value,
        target,
        threshold,
        window_ms,
        pid_file,
        log_file,
    )

    daemon = ChokerDaemon(
        monitor=CpuLoadMonitor(),
        burner=CpuBurnController(),
        threshold_percent=threshold,
        window_seconds=window_ms / 1000.0,
        strategy=strategy,
        target_percent=target,
        logger=logger,
    )

    def request_stop(signum, frame) -> None:
        del signum, frame
        logger.info("shutdown requested")
        daemon.request_stop()

    old_sigterm = signal.signal(signal.SIGTERM, request_stop)
    old_sigint = signal.signal(signal.SIGINT, request_stop)
    try:
        with PidFile(pid_file):
            daemon.run(max_iterations=max_iterations)
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
        logger.info("choker stopped")


def _validate_config(threshold: float, target: float, window_ms: int) -> None:
    if threshold < 0 or threshold > 100:
        raise click.BadParameter("threshold must be between 0 and 100")
    if target < 0 or target > 100:
        raise click.BadParameter("target must be between 0 and 100")
    if window_ms <= 0:
        raise click.BadParameter("window-ms must be greater than 0")


def _configure_logging(log_file: str | Path) -> None:
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _prepend_pythonpath(path: Path, current: str | None) -> str:
    if not current:
        return str(path)
    return f"{path}{os.pathsep}{current}"
