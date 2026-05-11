# burner

`burner` reads a normalized CSV curve and controls CPU and/or GPU burn intensity over time.

## Usage

```bash
./burner [--cpu] [--gpu] -f <curve.csv> -t <duration> -p <period> [-s <start_time>] [--tick <seconds>]
```

Examples:

```bash
./burner --cpu -f tests/fixtures/sine.csv -t 30m -p 60s
./burner --cpu --gpu -f tests/fixtures/sine.csv -t 1h -p 5m
./burner --cpu --gpu -f tests/fixtures/sine.csv -t 1h -p 5m -s "2026-05-10T12:00:00Z"
```

## Curve CSV

The curve file has no header and exactly two columns:

```csv
0.0,0.5
0.25,1.0
0.5,0.5
0.75,0.0
1.0,0.5
```

- First column: normalized period position `x`, from `0` to `1`.
- Second column: burn intensity `y`; values below `0` are clamped to `0`, values above `1` are clamped to `1`.
- Points must be strictly increasing by `x`.
- Values between points use linear interpolation.

## Timing

- `-t/--time` is the total run duration.
- `-p/--period` is one full curve period and supports decimal values, for example `0.5s`, `1.25m`.
- `-t/--time` still uses integer duration values.
- Supported duration units are `s`, `m`, and `h`, for example `20s`, `30m`, `1h`.
- The default scheduler tick is `0.1s`.
- `--tick` controls how often `burner` recalculates and writes the target intensity.

## Backends

- CPU uses the patched `third_party/lookbusy/lookbusy`.
- GPU uses patched `third_party/gpu-burn/gpu_burn` with `--burn-util-file` so the CUDA work loop reads the live target utilization and throttles kernel submission internally.
- GPU burn targets all detected CUDA GPUs by default. `burner` does not pass `-i`, so gpu-burn forks one worker per GPU and all workers read the same utilization control file.
- The patched CPU and GPU backends use `100ms` control checks for externally supplied utilization targets.

Build CPU support:

```bash
bash scripts/build_lookbusy.sh
```

Build GPU support:

```bash
bash scripts/build_gpu_burn.sh
```

If a required backend binary is missing, `burner` exits with a clear error.
