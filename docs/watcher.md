# watcher

`watcher` samples CPU/GPU power, renders a terminal TUI, and writes CSV data.

## Usage

```bash
./watcher -n <interval> -f <output.csv> [--mock]
```

Examples:

```bash
./watcher -n 0.1 -f power.csv
./watcher --mock -n 0.1 -f /tmp/power.csv
```

## CSV Output

The output file is overwritten and starts with this header:

```csv
timestamp,cpu_watts,gpu_watts
```

- `timestamp` is UTC ISO time.
- `cpu_watts` is CPU power in watts.
- `gpu_watts` is GPU power in watts.
- Missing hardware data is written as an empty field.

## Sampling Sources

- CPU power uses Linux RAPL under `/sys/class/powercap`.
- GPU power uses `nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits`.
- `--mock` disables hardware access and emits generated periodic sample data.

On some systems, RAPL `energy_uj` files are readable only by `root`. In that case the TUI status shows `RAPL permission denied`, CPU CSV fields stay empty, and running `watcher` with `sudo` or adjusting local permissions is required for CPU power.

## TUI

The TUI uses Rich when the `rich` package is installed.

The default view is full-screen and similar to `nvtop`:

- A header shows current CPU/GPU power, min/avg/max, CSV path, and sampler status.
- The main area shows scrolling multi-line CPU and GPU power curves.
- Missing sensor values are shown as gaps in the chart and empty fields in CSV.
- Ctrl-C stops sampling and closes the CSV file cleanly.

If Rich is unavailable, the command falls back to a simple terminal status line instead of failing before data can be recorded.
