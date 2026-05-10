# burner

`burner` provides two command-line tools:

- `./burner`: burn CPU and/or GPU according to a periodic CSV curve.
- `./watcher`: sample CPU/GPU power, draw a terminal TUI, and save CSV data.

The Python control layer lives in `warpper/`. The directory name is intentionally kept as-is for now.

## Quick Start

Use the project Conda environment if available:

```bash
conda activate ReLL
```

Run tests:

```bash
bash scripts/test.sh
```

Build CPU burn support:

```bash
bash scripts/build_lookbusy.sh
```

Build GPU burn support:

```bash
bash scripts/build_gpu_burn.sh
```

Run a short CPU burn using the sample sine curve:

```bash
./burner --cpu -f tests/fixtures/sine.csv -t 5s -p 2s
```

Run a short GPU burn:

```bash
./burner --gpu -f tests/fixtures/sine.csv -t 5s -p 2s
```

Watch mock power data and save CSV:

```bash
./watcher --mock -n 0.1 -f /tmp/power.csv
```

Watch real hardware power and save CSV:

```bash
./watcher -n 0.1 -f power.csv
```

## burner Usage

```bash
./burner [--cpu] [--gpu] -f <curve.csv> -t <duration> -p <period> [-s <start_time>]
```

Options:

| Option | Meaning |
| --- | --- |
| `--cpu` | Enable CPU burn through patched `lookbusy`. |
| `--gpu` | Enable GPU burn through `gpu_burn` duty-cycle control. |
| `-f`, `--file` | Input curve CSV. |
| `-t`, `--time` | Total run duration, such as `20s`, `30m`, `1h`. |
| `-p`, `--period` | Duration of one full curve period. |
| `-s`, `--start` | Optional UTC start time, such as `2026-05-10T12:00:00Z`. |

At least one of `--cpu` or `--gpu` is required. If both are provided, CPU and GPU are controlled together using the same curve.

## Curve CSV Format

The curve CSV has no header and exactly two columns:

```csv
0.0,0.5
0.25,1.0
0.5,0.5
0.75,0.0
1.0,0.5
```

- Column 1 is normalized period position `x`, from `0` to `1`.
- Column 2 is burn intensity `y`.
- `y < 0` is clamped to `0`.
- `y > 1` is clamped to `1`.
- `x` values must be strictly increasing.
- Values between points use linear interpolation.

`tests/fixtures/sine.csv` is a usable sample curve.

## watcher Usage

```bash
./watcher -n <interval> -f <output.csv> [--mock]
```

Options:

| Option | Meaning |
| --- | --- |
| `-n` | Sampling interval in seconds; decimals such as `0.1` are allowed. |
| `-f`, `--file` | Output CSV path. Existing files are overwritten. |
| `--mock` | Use generated mock CPU/GPU power data instead of hardware sensors. |

Output CSV columns:

```csv
timestamp,cpu_watts,gpu_watts
```

CPU power is read from Linux RAPL under `/sys/class/powercap`. GPU power is read through `nvidia-smi`. Missing hardware data is written as an empty CSV field.

By default, `watcher` opens a Rich full-screen terminal view similar to `nvtop`: current CPU/GPU power at the top, scrolling multi-line CPU and GPU power curves in the main area, and CSV/status information at the bottom. Use Ctrl-C to exit cleanly.

## Local Smoke Tests

The following commands are useful before running longer burns:

```bash
bash scripts/test.sh
bash scripts/build_lookbusy.sh
bash scripts/build_gpu_burn.sh
./burner --cpu -f tests/fixtures/sine.csv -t 1s -p 1s --tick 0.25
./burner --gpu -f tests/fixtures/sine.csv -t 1s -p 1s --tick 0.25
./watcher --mock -n 0.1 -f /tmp/watcher-smoke.csv --samples 3 --no-tui
```

`--tick`, `--samples`, and `--no-tui` are mainly for development and smoke testing.

## Troubleshooting

- `lookbusy binary not found`: run `bash scripts/build_lookbusy.sh`.
- `gpu_burn binary not found`: run `bash scripts/build_gpu_burn.sh`.
- CUDA / `nvcc` missing: install CUDA or set `CUDAPATH` before building GPU support.
- CPU power is empty in watcher CSV: check the TUI status. On many Linux systems RAPL `energy_uj` files are `root` readable only; run `sudo ./watcher ...` or adjust local permissions/udev rules.
- GPU power is empty in watcher CSV: check `nvidia-smi` and GPU driver availability.
- Rich TUI is unavailable: install `rich`, or the watcher will fall back to a simple terminal status line.

## Documentation

More detail is available in:

- `docs/requirements.md`
- `docs/burner.md`
- `docs/watcher.md`
- `docs/third_party_changes.md`
