from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


WORKLOADS = ("crypto", "compress", "compile", "python-cpu")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run mixed CPU workload workers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-job")
    run_parser.add_argument("--job-id", required=True)
    run_parser.add_argument("--workload", choices=WORKLOADS, required=True)
    run_parser.add_argument("--workers", type=int, required=True)
    run_parser.add_argument("--duration-seconds", type=float, required=True)
    run_parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    if args.command == "run-job":
        return run_job(
            job_id=args.job_id,
            workload=args.workload,
            workers=args.workers,
            duration_seconds=args.duration_seconds,
            seed=args.seed,
        )
    return 2


def run_job(
    job_id: str,
    workload: str,
    workers: int,
    duration_seconds: float,
    seed: int,
) -> int:
    if workers <= 0:
        print("workers must be greater than 0", file=sys.stderr)
        return 2
    if duration_seconds <= 0:
        print("duration-seconds must be greater than 0", file=sys.stderr)
        return 2

    stop_event = mp.Event()
    processes: list[mp.Process] = []
    temp_root = Path(tempfile.mkdtemp(prefix=f"burner-workload-{job_id}-"))

    def request_stop(signum, frame) -> None:
        del signum, frame
        stop_event.set()

    old_sigterm = signal.signal(signal.SIGTERM, request_stop)
    old_sigint = signal.signal(signal.SIGINT, request_stop)
    try:
        deadline = time.monotonic() + duration_seconds
        print(
            f"starting workload job={job_id} workload={workload} workers={workers} "
            f"duration={duration_seconds:.3f}s temp={temp_root}",
            flush=True,
        )
        for index in range(workers):
            process = mp.Process(
                target=_worker_main,
                args=(workload, index, deadline, stop_event, temp_root, seed + index),
                name=f"{workload}-{index}",
            )
            process.start()
            processes.append(process)

        while time.monotonic() < deadline and not stop_event.is_set():
            time.sleep(0.2)
        stop_event.set()

        for process in processes:
            process.join(timeout=5)
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=2)
        for process in processes:
            if process.is_alive():
                process.kill()
        print(f"finished workload job={job_id}", flush=True)
        return 0
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
        shutil.rmtree(temp_root, ignore_errors=True)


def _worker_main(
    workload: str,
    index: int,
    deadline: float,
    stop_event: mp.Event,
    temp_root: Path,
    seed: int,
) -> None:
    random.seed(seed)
    worker_dir = temp_root / f"worker-{index}"
    worker_dir.mkdir(parents=True, exist_ok=True)
    try:
        if workload == "crypto":
            _crypto_loop(deadline, stop_event, worker_dir)
        elif workload == "compress":
            _compress_loop(deadline, stop_event, worker_dir)
        elif workload == "compile":
            _compile_loop(deadline, stop_event, worker_dir, seed)
        elif workload == "python-cpu":
            _python_cpu_loop(deadline, stop_event, seed)
        else:
            raise ValueError(f"unknown workload: {workload}")
    except KeyboardInterrupt:
        stop_event.set()


def _crypto_loop(deadline: float, stop_event: mp.Event, cwd: Path) -> None:
    if _has_command("openssl"):
        while _running(deadline, stop_event):
            _run(["openssl", "speed", "-elapsed", "-seconds", "2", "sha256"], cwd=cwd)
        return
    _python_cpu_loop(deadline, stop_event, 0)


def _compress_loop(deadline: float, stop_event: mp.Event, cwd: Path) -> None:
    data_path = cwd / "input.bin"
    if not data_path.exists():
        with data_path.open("wb") as handle:
            for _ in range(32):
                handle.write(os.urandom(1024 * 1024))

    if _has_command("pigz"):
        command = ["pigz", "-c", "-11", str(data_path)]
    elif _has_command("xz"):
        command = ["xz", "-c", "-T1", "-9", str(data_path)]
    else:
        _python_cpu_loop(deadline, stop_event, 0)
        return

    while _running(deadline, stop_event):
        with open(os.devnull, "wb") as devnull:
            _run(command, cwd=cwd, stdout=devnull)


def _compile_loop(
    deadline: float,
    stop_event: mp.Event,
    cwd: Path,
    seed: int,
) -> None:
    if not (_has_command("gcc") and _has_command("make")):
        _python_cpu_loop(deadline, stop_event, seed)
        return
    _write_compile_project(cwd, seed)
    while _running(deadline, stop_event):
        _run(["make", "clean"], cwd=cwd)
        _run(["make", "-j1", "all"], cwd=cwd)


def _python_cpu_loop(deadline: float, stop_event: mp.Event, seed: int) -> None:
    payload = str(seed).encode("utf-8") * 128
    counter = 0
    while _running(deadline, stop_event):
        digest = payload
        for _ in range(5000):
            digest = hashlib.sha256(digest).digest()
        counter += digest[0]
        if counter % 1000 == 0:
            payload = hashlib.sha512(payload + digest).digest()


def _write_compile_project(cwd: Path, seed: int) -> None:
    sources = []
    for index in range(8):
        source = cwd / f"unit_{index}.c"
        source.write_text(_c_source(index, seed), encoding="utf-8")
        sources.append(source.name)
    main_source = cwd / "main.c"
    declarations = "\n".join(f"int unit_{index}(int value);" for index in range(8))
    calls = "\n".join(f"    total += unit_{index}(total);" for index in range(8))
    main_source.write_text(
        f"{declarations}\nint main(void) {{\n    int total = {seed % 97};\n{calls}\n    return total == 0;\n}}\n",
        encoding="utf-8",
    )
    objects = " ".join(source.replace(".c", ".o") for source in sources + ["main.c"])
    makefile = textwrap.dedent(
        f"""
        CC ?= gcc
        CFLAGS ?= -O2 -pipe
        OBJS = {objects}

        all: workload

        workload: $(OBJS)
        \t$(CC) $(CFLAGS) -o $@ $(OBJS)

        %.o: %.c
        \t$(CC) $(CFLAGS) -c $< -o $@

        clean:
        \trm -f $(OBJS) workload
        """
    ).lstrip()
    (cwd / "Makefile").write_text(makefile, encoding="utf-8")


def _c_source(index: int, seed: int) -> str:
    return textwrap.dedent(
        f"""
        static int mix_{index}(int value) {{
            for (int i = 0; i < 20000; ++i) {{
                value = (value * 1103515245 + {seed + index + 12345}) & 0x7fffffff;
                value ^= value >> ((i % 7) + 1);
            }}
            return value;
        }}

        int unit_{index}(int value) {{
            int total = value + {index + 1};
            for (int i = 0; i < 64; ++i) {{
                total ^= mix_{index}(total + i);
            }}
            return total;
        }}
        """
    ).lstrip()


def _run(command: list[str], cwd: Path, stdout=None) -> None:
    try:
        subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return


def _has_command(command: str) -> bool:
    return shutil.which(command) is not None


def _running(deadline: float, stop_event: mp.Event) -> bool:
    return not stop_event.is_set() and time.monotonic() < deadline


if __name__ == "__main__":
    raise SystemExit(main())
