from __future__ import annotations

import csv
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PowerSample:
    timestamp: datetime
    cpu_watts: float | None
    gpu_watts: float | None


class RaplSampler:
    def __init__(
        self,
        root: Path = Path("/sys/class/powercap"),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.root = Path(root)
        self.clock = clock
        self.status = "not sampled"
        self._previous_energy_uj: int | None = None
        self._previous_time: float | None = None

    def sample(self) -> float | None:
        energy = self._read_energy_uj()
        if energy is None:
            if self.status == "not sampled":
                self.status = "RAPL missing"
            return None

        now = self.clock()
        if self._previous_energy_uj is None or self._previous_time is None:
            self._previous_energy_uj = energy
            self._previous_time = now
            self.status = "warming up"
            return None

        elapsed = now - self._previous_time
        delta = energy - self._previous_energy_uj
        self._previous_energy_uj = energy
        self._previous_time = now
        if elapsed <= 0 or delta < 0:
            self.status = "invalid RAPL delta"
            return None

        self.status = "ok"
        return (delta / 1_000_000.0) / elapsed

    def _read_energy_uj(self) -> int | None:
        if not self.root.exists():
            self.status = "RAPL missing"
            return None
        values = []
        permission_denied = False
        for path in self._energy_paths():
            try:
                values.append(int(path.read_text(encoding="utf-8").strip()))
            except PermissionError:
                permission_denied = True
            except (OSError, ValueError):
                continue
        if permission_denied and not values:
            self.status = "RAPL permission denied"
            return None
        if not values:
            self.status = "RAPL missing"
            return None
        return sum(values)

    def _energy_paths(self):
        paths = []
        for path in self.root.glob("intel-rapl*/energy_uj"):
            paths.append(path)
        for path in self.root.rglob("energy_uj"):
            paths.append(path)
        return list(dict.fromkeys(paths))


class NvidiaSmiSampler:
    def __init__(self, runner=subprocess.run):
        self.runner = runner
        self.status = "not sampled"

    def sample(self) -> float | None:
        command = [
            "nvidia-smi",
            "--query-gpu=power.draw",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = self.runner(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=2,
            )
        except FileNotFoundError:
            self.status = "nvidia-smi missing"
            return None
        except subprocess.SubprocessError:
            self.status = "nvidia-smi failed"
            return None

        if result.returncode != 0:
            self.status = "nvidia-smi failed"
            return None

        values = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                values.append(float(line))
            except ValueError:
                self.status = "invalid nvidia-smi output"
                return None
        if not values:
            self.status = "GPU power unavailable"
            return None

        self.status = "ok"
        return sum(values)


class CombinedPowerSampler:
    def __init__(
        self,
        cpu: RaplSampler | None = None,
        gpu: NvidiaSmiSampler | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.cpu = cpu or RaplSampler()
        self.gpu = gpu or NvidiaSmiSampler()
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def status(self) -> str:
        return f"CPU: {self.cpu.status}; GPU: {self.gpu.status}"

    def sample(self) -> PowerSample:
        return PowerSample(
            timestamp=self.now(),
            cpu_watts=self.cpu.sample(),
            gpu_watts=self.gpu.sample(),
        )


class MockPowerSampler:
    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.index = 0
        self.status = "mock mode"

    def sample(self) -> PowerSample:
        phase = self.index / 20.0
        self.index += 1
        cpu = 45.0 + 20.0 * (math.sin(phase * math.tau) + 1.0) / 2.0
        gpu = 90.0 + 50.0 * (math.sin((phase + 0.25) * math.tau) + 1.0) / 2.0
        return PowerSample(timestamp=self.now(), cpu_watts=cpu, gpu_watts=gpu)


def run_watcher(
    interval: float,
    output_path: str | Path,
    sampler,
    max_samples: int | None = None,
    tui: bool = True,
    sleep: bool = True,
) -> None:
    if interval <= 0:
        raise ValueError("interval must be greater than 0")
    output_path = Path(output_path)
    if output_path.parent and not output_path.parent.exists():
        raise ValueError(f"output directory does not exist: {output_path.parent}")

    display = create_tui(enabled=tui, sampler=sampler, output_path=output_path)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "cpu_watts", "gpu_watts"])
        count = 0
        try:
            while max_samples is None or count < max_samples:
                sample = sampler.sample()
                writer.writerow(
                    [
                        _format_timestamp(sample.timestamp),
                        _format_number(sample.cpu_watts),
                        _format_number(sample.gpu_watts),
                    ]
                )
                handle.flush()
                display.update(sample)
                count += 1
                if max_samples is not None and count >= max_samples:
                    break
                if sleep:
                    time.sleep(interval)
        finally:
            display.close()


def create_tui(enabled: bool, sampler, output_path: Path):
    if not enabled:
        return NullTui()
    try:
        return RichPowerTui(sampler=sampler, output_path=output_path)
    except ImportError:
        return PlainPowerTui(sampler=sampler, output_path=output_path)


class NullTui:
    def update(self, sample: PowerSample) -> None:
        del sample

    def close(self) -> None:
        pass


class PlainPowerTui:
    def __init__(self, sampler, output_path: Path):
        self.sampler = sampler
        self.output_path = output_path

    def update(self, sample: PowerSample) -> None:
        print(
            f"{_format_timestamp(sample.timestamp)} "
            f"CPU={_format_number(sample.cpu_watts) or '-'}W "
            f"GPU={_format_number(sample.gpu_watts) or '-'}W "
            f"{getattr(self.sampler, 'status', '')} -> {self.output_path}",
            flush=True,
        )

    def close(self) -> None:
        pass


class RichPowerTui:
    def __init__(self, sampler, output_path: Path):
        from rich.live import Live

        self.sampler = sampler
        self.output_path = output_path
        self.cpu_history: list[float | None] = []
        self.gpu_history: list[float | None] = []
        self._live = Live(
            self._render(None),
            refresh_per_second=8,
            screen=True,
            transient=False,
        )
        self._live.start()

    def update(self, sample: PowerSample) -> None:
        self.cpu_history.append(sample.cpu_watts)
        self.gpu_history.append(sample.gpu_watts)
        self.cpu_history = self.cpu_history[-60:]
        self.gpu_history = self.gpu_history[-60:]
        self._live.update(self._render(sample))

    def close(self) -> None:
        self._live.stop()

    def _render(self, sample: PowerSample | None):
        from rich.console import Group
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        layout = Layout(name="root")
        layout.split_column(
            Layout(name="header", size=7),
            Layout(name="charts"),
            Layout(name="footer", size=3),
        )
        layout["charts"].split_column(
            Layout(name="cpu"),
            Layout(name="gpu"),
        )

        stats = Table.grid(expand=True)
        stats.add_column(ratio=1)
        stats.add_column(ratio=1)
        stats.add_column(ratio=2)
        stats.add_row(
            "[bold cyan]CPU[/bold cyan]",
            _format_watts(sample.cpu_watts) if sample else "-",
            _stats_text(self.cpu_history),
        )
        stats.add_row(
            "[bold green]GPU[/bold green]",
            _format_watts(sample.gpu_watts) if sample else "-",
            _stats_text(self.gpu_history),
        )
        stats.add_row("CSV", str(self.output_path), getattr(self.sampler, "status", ""))
        layout["header"].update(Panel(stats, title="Power Watcher", border_style="blue"))

        cpu_chart = Text(
            render_power_chart("CPU Power", self.cpu_history, width=72, height=12),
            style="cyan",
        )
        gpu_chart = Text(
            render_power_chart("GPU Power", self.gpu_history, width=72, height=12),
            style="green",
        )
        layout["cpu"].update(Panel(cpu_chart, border_style="cyan"))
        layout["gpu"].update(Panel(gpu_chart, border_style="green"))

        footer = Group(
            Text("Press Ctrl-C to stop. Data is written continuously to CSV.", style="dim"),
            Text("Missing sensor values are shown as gaps and saved as empty CSV fields.", style="dim"),
        )
        layout["footer"].update(Panel(footer, border_style="blue"))
        return layout


def render_power_chart(
    title: str,
    values: list[float | None],
    width: int = 72,
    height: int = 12,
    unit: str = "W",
) -> str:
    numeric = [value for value in values if value is not None]
    if not numeric:
        empty = [" " * width for _ in range(height)]
        return "\n".join([f"{title}  no data", *[f"       |{line}" for line in empty]])

    window = values[-width:]
    lo = min(numeric)
    hi = max(numeric)
    if hi == lo:
        hi = lo + 1.0
    span = hi - lo
    rows = [[" " for _ in range(width)] for _ in range(height)]

    left_pad = width - len(window)
    previous: tuple[int, int] | None = None
    for index, value in enumerate(window):
        if value is None:
            previous = None
            continue
        column = left_pad + index
        scaled = (value - lo) / span
        row = height - 1 - int(round(scaled * (height - 1)))
        row = max(0, min(height - 1, row))
        if previous is None:
            rows[row][column] = "─"
        else:
            _draw_line(rows, previous, (column, row))
        previous = (column, row)

    lines = [f"{title}  current {_format_watts(values[-1])}"]
    for row_index, cells in enumerate(rows):
        if row_index == 0:
            label = f"{hi:.1f}{unit}"
        elif row_index == height - 1:
            label = f"{lo:.1f}{unit}"
        else:
            label = ""
        lines.append(f"{label:>7} |{''.join(cells)}")
    lines.append(f"{'':>7} +{'-' * width}")
    return "\n".join(lines)


def _draw_line(rows: list[list[str]], start: tuple[int, int], end: tuple[int, int]) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    steps = max(abs(dx), abs(dy), 1)
    previous = start
    for step in range(1, steps + 1):
        x = round(x0 + dx * step / steps)
        y = round(y0 + dy * step / steps)
        if x == previous[0]:
            char = "│"
        elif y == previous[1]:
            char = "─"
        elif y < previous[1]:
            char = "╱"
        else:
            char = "╲"
        _put_chart_char(rows, x, y, char)
        previous = (x, y)


def _put_chart_char(rows: list[list[str]], x: int, y: int, char: str) -> None:
    if y < 0 or y >= len(rows) or x < 0 or x >= len(rows[y]):
        return
    existing = rows[y][x]
    rows[y][x] = char if existing == " " or existing == char else "┼"


def _stats_text(values: list[float | None]) -> str:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return "min -   avg -   max -"
    return (
        f"min {min(numeric):.1f} W   "
        f"avg {sum(numeric) / len(numeric):.1f} W   "
        f"max {max(numeric):.1f} W"
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_number(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _format_watts(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f} W"
