# Third-Party Changes

## lookbusy

File changed:

- `third_party/lookbusy/lb.c`

Reason:

- `burner` needs to update CPU burn percentage dynamically from a CSV curve. The upstream lookbusy CLI accepts fixed or built-in curve parameters, but it does not accept an externally controlled live utilization target.

Change:

- Added `--cpu-util-file=PATH`.
- CPU spinner processes read a percentage from this file on each control loop.
- Values below `0` are treated as `0`; values above `100` are treated as `100`.
- If the file cannot be read or parsed, lookbusy falls back to its configured utilization.

Compatibility:

- Existing lookbusy options remain unchanged.
- `--cpu-util-file` is additive.

## gpu-burn

No source files are changed for the first implementation.

`burner` controls GPU intensity externally by starting one long-running `gpu_burn` process and using duty-cycle `SIGSTOP` / `SIGCONT` control over its process group. This avoids deep CUDA kernel changes in the first version.
