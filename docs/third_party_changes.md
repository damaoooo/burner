# Third-Party Changes

## lookbusy

File changed:

- `third_party/lookbusy/lb.c`

Reason:

- `burner` needs to update CPU burn percentage dynamically from a CSV curve. The upstream lookbusy CLI accepts fixed or built-in curve parameters, but it does not accept an externally controlled live utilization target.

Change:

- Added `--cpu-util-file=PATH`.
- CPU spinner processes read a percentage from this file on each control loop.
- The external control loop uses a `10ms` window to track finer input curves.
- Values below `0` are treated as `0`; values above `100` are treated as `100`.
- If the file cannot be read or parsed, lookbusy falls back to its configured utilization.

Compatibility:

- Existing lookbusy options remain unchanged.
- `--cpu-util-file` is additive.

## gpu-burn

File changed:

- `third_party/gpu-burn/gpu_burn-drv.cpp`

Reason:

- External `SIGSTOP` / `SIGCONT` process control cannot reliably limit GPU utilization because CUDA kernels already submitted to the device keep running. `burner` needs the GPU load generator itself to throttle kernel submission according to the live curve target.

Change:

- Added `--burn-util-file FILE`.
- Child GPU workers read the target utilization percentage from this file.
- `0` means do not submit GPU work.
- `100` means run continuously.
- Intermediate values throttle inside the CUDA work loop by measuring completed work time and sleeping between iterations.
- Sleep chunks are capped at `10ms` so utilization changes are noticed promptly.
- The Python backend starts gpu-burn with a smaller default memory window so each control cycle has finer granularity.
- Invalid or unreadable control file values are treated as `0` when the option is used.

Compatibility:

- Existing gpu-burn options remain unchanged.
- `--burn-util-file` is additive and used by the Python `burner` GPU backend.
