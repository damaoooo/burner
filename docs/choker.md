# choker Requirements

`choker` is an idle CPU load daemon for `burner`. It keeps CPUs busy only while
the host is otherwise idle, and yields as soon as other applications need CPU.

## Scope

- Version 1 is CPU-only.
- GPU execution is out of scope for version 1, but the implementation should keep
  burn backend interfaces extensible for a future GPU backend.
- `choker` must not require root privileges for CPU operation.
- `choker` must reuse the existing CPU burn capability when practical instead of
  adding an unrelated CPU burner.

## CLI Contract

The command entrypoint is:

```bash
python -m choker <command>
```

Required commands:

| Command | Behavior |
| --- | --- |
| `start` | Start a background daemon. |
| `stop` | Stop the daemon and any choker-owned CPU burn. |
| `status` | Show whether the daemon is running, stopped, or has a stale pidfile. |
| `run` | Run the daemon in the foreground for debugging and tests. |

Default runtime files:

- pidfile: `.runtime/choker.pid`
- log file: `.runtime/choker.log`

Required options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--strategy` | `complement` | CPU fill strategy: `complement` or `idle`. |
| `--target` | `100.0` | Target aggregate CPU utilization for `complement` strategy. |
| `--threshold` | `10.0` | External CPU threshold for `idle` strategy. |
| `--window-ms` | `1000` | CPU sampling window, in milliseconds. |
| `--pid-file` | `.runtime/choker.pid` | pidfile path override. |
| `--log-file` | `.runtime/choker.log` | log file path override. |

`--threshold` must be in the range `0..100`.
`--target` must be in the range `0..100`.
`--window-ms` must be greater than `0`.

## CPU Accounting

CPU utilization must be measured as aggregate utilization across all logical CPU
cores and normalized to a `0..100` scale.

This means:

- If every core is fully busy, utilization is `100`, not `N * 100`.
- If half of total CPU capacity is busy, utilization is `50`.
- The implementation must not treat a single saturated core on an `N`-core host
  as `100` unless `N == 1`.

When choker owns an active burn process, it must subtract its own burn CPU usage
from the aggregate system CPU usage:

```text
external_cpu = max(0, total_cpu - choker_owned_cpu)
```

The daemon must make decisions from `external_cpu`, not raw total CPU, otherwise
its own burn would make the machine appear busy and cause self-triggered stop
loops.

## Daemon Behavior

The daemon repeatedly samples CPU over the configured `--window-ms` window.

- In `complement` strategy, choker sets CPU burn intensity to fill the gap:
  `max(0, target - external_cpu) / 100`.
- In `idle` strategy, if `external_cpu < threshold`, choker starts CPU burn at
  100% intensity.
- In `idle` strategy, if `external_cpu >= threshold`, choker stops its own CPU
  burn.
- After yielding or reducing intensity, choker keeps monitoring and
  automatically resumes or increases burn when spare CPU capacity appears again.
- To reduce low-utilization gaps, the CPU backend may stay prewarmed at 0%
  intensity after yielding; daemon shutdown must still terminate it.
- Repeated samples in the same effective state must not repeatedly restart or
  restop the backend.
- Shutdown via `stop`, SIGTERM, SIGINT, or normal `run` exit must stop any active
  choker-owned burn and remove the pidfile.

CPU burn must cover all logical CPUs. The existing `LookbusyCpuBackend` already
supports this by default.

## Burner Square-Wave Integration Goal

The primary integration goal is to smooth CPU utilization and power when
`burner` runs a square-wave CPU load.

In that scenario:

- during the high phase of the `burner` square wave, choker must treat burner as
  external CPU load and reduce or stop its own burn;
- during the low phase of the `burner` square wave, choker must fill the idle CPU
  capacity so total CPU utilization stays high;
- choker should reduce low-utilization gaps and power oscillation as much as the
  configured sampling window and backend control latency allow;
- short, bounded integration checks may run real CPU burn to compare burner-only
  utilization against burner-plus-choker utilization, but automated tests should
  keep using mocked burn backends by default.

The same target-filling behavior must also support smooth waves such as sine
waves. For sine-wave burner input, choker should continuously fill the remaining
CPU capacity instead of only switching at the sine-wave trough.

## Logging

`choker` must write logs to the configured log file. Logs should include:

- daemon startup and shutdown;
- effective threshold, window, pidfile, and log paths;
- CPU sample summaries;
- burn start and stop transitions;
- backend errors;
- pidfile handling, including stale pidfiles.

## Tests

Tests must not perform real CPU burn.

Required test coverage:

- idle below threshold starts burn;
- external load at or above threshold stops burn;
- idle after stopping resumes burn;
- unchanged state does not repeatedly start or stop;
- backend startup failure is logged and leaves a clean state;
- shutdown stops active burn;
- multi-core CPU accounting normalizes to `100`, not `N * 100`;
- choker-owned CPU is subtracted from aggregate CPU;
- missing or exited choker-owned process counts as zero owned CPU;
- negative external load is clamped to zero;
- invalid CLI thresholds and windows fail clearly;
- running, stopped, and stale pidfile status cases.
