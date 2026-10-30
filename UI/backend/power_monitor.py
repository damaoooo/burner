from __future__ import annotations

import asyncio
import shlex
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol


Broadcast = Callable[[dict[str, object]], Awaitable[None]]

DEFAULT_INTERVAL = 0.1
MIN_INTERVAL = 0.02
MAX_INTERVAL = 5.0
KEEP_SECONDS = 1800.0
# A bin is published once every node has reported it, or after this long.
SETTLE_SECONDS = 1.0
FLUSH_SECONDS = 0.25

# Runs on each node as root (RAPL energy files are root-only). Samples are
# taken on absolute wall-clock boundaries, so NTP-synced nodes produce the
# same bin labels and their power can be summed bin by bin. Each line is
# "<bin start epoch ms>,<watts over the bin>". The script exits when its
# stdout closes (monitor stopped) or after 12 hours.
SAMPLER_SCRIPT = r"""
import glob, os, sys, time
iv = float(sys.argv[1])
dirs = sorted(d for d in glob.glob("/sys/class/powercap/intel-rapl:*") if d.count(":") == 1)
if not dirs:
    sys.exit("no RAPL package domains under /sys/class/powercap")
files = [open(d + "/energy_uj") for d in dirs]
ranges = [int(open(d + "/max_energy_range_uj").read()) for d in dirs]
def read():
    values = []
    for f in files:
        f.seek(0)
        values.append(int(f.read()))
    return values
deadline = time.time() + 12 * 3600
k = int(time.time() / iv) + 1
time.sleep(max(0.0, k * iv - time.time()))
prev, prev_t = read(), time.time()
k += 1
while time.time() < deadline:
    target = k * iv
    time.sleep(max(0.0, target - time.time()))
    cur, now = read(), time.time()
    joules = sum((c - p) % r for c, p, r in zip(cur, prev, ranges)) / 1e6
    print("%d,%.2f" % (round((target - iv) * 1000), joules / (now - prev_t)), flush=True)
    prev, prev_t = cur, now
    k = max(k + 1, int(time.time() / iv) + 1)
"""


class PowerMonitorError(RuntimeError):
    pass


class SSHLike(Protocol):
    def status_for(self, machine_id: str) -> str: ...
    def get_connection(self, machine_id: str): ...


def sampler_command(interval: float) -> str:
    return f"sudo -n python3 -u -c {shlex.quote(SAMPLER_SCRIPT)} {interval:g}"


def parse_sample_line(line: str) -> tuple[float, float] | None:
    parts = line.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) / 1000, float(parts[1])
    except ValueError:
        return None


class PowerAggregator:
    """Sums per-node power samples that share a time bin."""

    def __init__(self, machine_ids: list[str], keep_seconds: float = KEEP_SECONDS):
        self.machine_ids = list(machine_ids)
        self.keep_seconds = keep_seconds
        self.latest: dict[str, float] = {}
        self._pending: dict[int, dict[str, float]] = {}
        self._last_key: int | None = None
        self._rows: deque[tuple[float, float, int, list[float | None]]] = deque()

    def add(self, machine_id: str, t: float, watts: float) -> None:
        key = round(t * 1000)
        if self._last_key is not None and key <= self._last_key:
            return
        self._pending.setdefault(key, {})[machine_id] = watts
        self.latest[machine_id] = watts

    def _per_node(self, values: dict[str, float]) -> list[float | None]:
        return [round(values[m], 2) if m in values else None for m in self.machine_ids]

    def finalize(
        self, now: float, settle: float = SETTLE_SECONDS
    ) -> list[tuple[float, float, int, list[float | None]]]:
        """Publish bins in time order once complete or older than ``settle``.

        Each row is (bin start, total W, nodes reporting, per-node W in
        ``machine_ids`` order with None for a node that did not report).
        """
        rows = []
        for key in sorted(self._pending):
            values = self._pending[key]
            if len(values) < len(self.machine_ids) and key / 1000 > now - settle:
                break
            del self._pending[key]
            self._last_key = key
            row = (key / 1000, round(sum(values.values()), 2), len(values), self._per_node(values))
            self._rows.append(row)
            rows.append(row)
        if self._rows:
            oldest = self._rows[-1][0] - self.keep_seconds - 1e-9
            while self._rows and self._rows[0][0] < oldest:
                self._rows.popleft()
        return rows

    def history(self, seconds: float | None = None) -> list[tuple[float, float, int, list[float | None]]]:
        rows = list(self._rows)
        if seconds is not None and rows:
            rows = [row for row in rows if row[0] >= rows[-1][0] - seconds - 1e-9]
        return rows

    def to_csv(self) -> str:
        lines = [",".join(["epoch_s", "total_W", "nodes", *self.machine_ids])]
        for t, total, count, per_node in self._rows:
            cells = ["" if value is None else f"{value:.2f}" for value in per_node]
            lines.append(",".join([f"{t:.3f}", f"{total:.2f}", str(count), *cells]))
        return "\n".join(lines) + "\n"


class PowerMonitor:
    def __init__(
        self,
        ssh: SSHLike,
        broadcast: Broadcast,
        clock: Callable[[], float] = time.time,
        keep_seconds: float = KEEP_SECONDS,
    ):
        self._ssh = ssh
        self._broadcast = broadcast
        self._clock = clock
        self._keep_seconds = keep_seconds
        self._aggregator: PowerAggregator | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._processes: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._interval = DEFAULT_INTERVAL
        self._started_at: float | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, machine_ids: list[str], interval: float = DEFAULT_INTERVAL) -> dict[str, object]:
        if self._running:
            raise PowerMonitorError("power monitor is already running")
        if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise PowerMonitorError(f"interval must be between {MIN_INTERVAL} and {MAX_INTERVAL} s")
        if not machine_ids:
            raise PowerMonitorError("at least one connected machine is required")
        for machine_id in machine_ids:
            if self._ssh.status_for(machine_id) != "connected":
                raise PowerMonitorError(f"machine {machine_id} is not connected")

        self._aggregator = PowerAggregator(machine_ids, self._keep_seconds)
        self._errors = {}
        self._interval = interval
        self._started_at = self._clock()
        self._running = True
        command = sampler_command(interval)
        self._tasks = [asyncio.create_task(self._read(machine_id, command)) for machine_id in machine_ids]
        self._tasks.append(asyncio.create_task(self._flush_loop()))
        status = self.status()
        await self._broadcast({"event": "power_status", **status})
        return status

    async def stop(self) -> dict[str, object]:
        if not self._running:
            return self.status()
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for process in self._processes.values():
            try:
                process.close()
            except Exception:
                pass
        self._tasks, self._processes = [], {}
        await self._publish(self._clock(), settle=0.0)
        status = self.status()
        await self._broadcast({"event": "power_status", **status})
        return status

    def status(self) -> dict[str, object]:
        agg = self._aggregator
        return {
            "running": self._running,
            "machines": agg.machine_ids if agg else [],
            "interval": self._interval,
            "started_at": self._started_at,
            "latest": dict(agg.latest) if agg else {},
            "errors": dict(self._errors),
        }

    def history(self, seconds: float | None = None) -> list[tuple[float, float, int, list[float | None]]]:
        return self._aggregator.history(seconds) if self._aggregator else []

    def to_csv(self) -> str:
        if self._aggregator is None:
            raise PowerMonitorError("no power data recorded yet")
        return self._aggregator.to_csv()

    async def _read(self, machine_id: str, command: str) -> None:
        assert self._aggregator is not None
        try:
            conn = self._ssh.get_connection(machine_id)
            async with conn.create_process(command) as process:
                self._processes[machine_id] = process
                async for line in process.stdout:
                    sample = parse_sample_line(line)
                    if sample is not None:
                        self._aggregator.add(machine_id, *sample)
                stderr = ""
                try:
                    stderr = await asyncio.wait_for(process.stderr.read(), timeout=1.0)
                except Exception:
                    pass
                self._errors[machine_id] = (stderr or "sampler exited").strip()[-300:]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._errors[machine_id] = str(exc)[-300:]
        if self._running:
            await self._broadcast({"event": "power_status", **self.status()})

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await self._publish(self._clock())

    async def _publish(self, now: float, settle: float = SETTLE_SECONDS) -> None:
        if self._aggregator is None:
            return
        rows = self._aggregator.finalize(now, settle)
        if rows:
            await self._broadcast(
                {
                    "event": "power_samples",
                    "samples": [list(row) for row in rows],
                    "latest": dict(self._aggregator.latest),
                }
            )
