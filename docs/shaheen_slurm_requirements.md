# Shaheen SLURM Requirements

## Summary

The `shaheen` branch migrates the WebUI from SSH-managed machines to a SLURM-managed CPU-only workflow for the Shaheen cluster. The UI runs on a login node in the Conda environment `burner`, submits a SLURM allocation, monitors worker initialization, and controls CPU burn plus watcher sampling through a shared filesystem control directory.

GPU burn remains visible only as a disabled Shaheen CPU-only capability. No implementation on this branch should depend on `UI/machines.json`.

## SLURM Allocation

- The UI submits the allocation. Users do not manually start compute-node workers.
- Submission requires only node count and time limit:
  - `-N <nodes>`
  - `--time=<duration>`
  - `--exclusive`
- Partition, account, and QOS are intentionally unsupported in the Shaheen UI.
- The backend should prefer `pyslurm` for job submit, state query, and cancellation. A command-line fallback must remain available for local testing and for Shaheen environments where matching PySlurm headers are unavailable.
- Shaheen currently reports Slurm `24.11.7`; a matching PySlurm build should use the `v24.11.0` PySlurm source tag and requires Slurm development headers. Do not install the PyPI latest blindly, because PySlurm major/minor must match the cluster Slurm major/minor. On Shaheen, the tested install uses the `burner` Conda environment on the login node, Slurm headers from the matching Slurm source tree, and `SLURM_LIB_DIR=/usr/lib64/slurm`.
- UI must show allocation state, SLURM job id, requested nodes, ready workers, polling interval, and release controls.
- Stop burn only stops the active burn command and keeps the allocation alive.
- Release allocation cancels the SLURM job and frees all nodes.

## Compute Worker Model

- Each allocated compute node runs one worker process through `srun --ntasks-per-node=1`.
- The worker rebuilds or uses the shared repo CPU backend, then reports readiness through the shared control directory.
- The repo path is shared between login and compute nodes, defaulting to `/scratch/zhoul0e/burner`.
- The shared control base defaults to `/scratch/zhoul0e/burner-slurm-control`.
- Compute nodes may not see the login-node `envs/burner` path. The sbatch script must select an executable worker Python in this order: explicit `BURNER_WORKER_PYTHON`, visible Conda env Python, `/scratch/$USER/miniconda3/bin/python3`, then `command -v python3`.
- The worker must launch the Python `burner` entrypoint through its own `sys.executable`, not through the script shebang, so compute nodes do not accidentally use an older system Python.
- Worker command polling interval is configured in the UI before submit and must support a 10 ms floor.
- Worker metric sampling and UI refresh default to `30 ms`, with a configurable floor of `30 ms`.
- Worker status files must include hostname, SLURM node name, IP address, CPU model, CPU count, memory, CPU TDP, heartbeat, latest watcher sample, and current worker state.
- Shaheen CPU nodes are treated as homogeneous for TDP reporting. Worker node info should report a fixed per-CPU TDP of `360 W`.

## Synchronization

- Burn start uses a barrier plus a future UTC timestamp.
- UI may start immediately or schedule a future start.
- Immediate mode still writes a near-future `start_at` after every worker is ready, so workers start from local clocks instead of from file detection time.
- Scheduled mode uses the user-provided future timestamp.
- Stop writes a shared stop command and workers terminate local burner processes as soon as they observe it.
- 10 ms-level synchronization is a target, not a hard real-time guarantee. Actual skew depends on shared filesystem metadata propagation, node clock sync, Python scheduling, and `lookbusy` response.

## CPU Burn And Watcher

- CPU burn runs on all CPUs on each node; do not pass a fixed CPU count to `lookbusy`.
- GPU burn must be disabled and must not be submitted to compute workers.
- Each worker samples local CPU power and appends node-local CSV samples under the session control directory.
- If CPU power cannot be sampled through RAPL, the worker reports an unavailable status without crashing and must expose a clearly marked estimated CPU wattage based on CPU utilization and Shaheen node TDP.
- Each worker should expose load-confirmation metrics in node state and CSV samples: estimated CPU watts, CPU utilization percent, average/min/max CPU frequency MHz, and 1-minute load average.
- High-frequency UI samples should update the latest node status file; CSV samples should be decimated to about once per second to reduce shared filesystem pressure.
- UI aggregates node state and latest watcher samples from worker status files.
- Each machine card should show a short live estimated-power chart. The frontend polling interval is user-configurable separately from the compute-worker command polling interval.

## UI Behavior

- Remove SSH connect/disconnect/update workflows from the Shaheen path.
- Machine cards represent allocated SLURM nodes, not static configured machines.
- The empty node state should tell users to submit a SLURM allocation, not to edit `UI/machines.json`.
- Start burn requires all allocated workers to be ready.
- GPU controls remain disabled with a Shaheen CPU-only note.
- Worker polling is set before allocation submit and cannot be rebuilt through the old remote sampling workflow.

## Testing Requirements

- Unit test SLURM submit script generation for `-N`, `--time`, and `--exclusive`, with no partition/account/QOS.
- Unit test the PySlurm-preferred SLURM client path using a fake client.
- Unit test shared control command writing for start and stop.
- Unit test the ready barrier: start must fail unless all requested workers are ready.
- Unit test CPU-only validation: GPU burn requests must be rejected.
- Use temporary directories to simulate `/scratch/zhoul0e/burner-slurm-control`.
- Frontend build must pass after replacing SSH machine flows with SLURM allocation flows.
