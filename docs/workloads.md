# Server-room workloads

The WebUI includes a real CPU workload mode for multi-node server-room
simulation. It is separate from `./burner` waveform burn: waveform burn still
uses patched `lookbusy`/`gpu_burn`, while workload mode runs real CPU-heavy
programs on each remote node.

## Remote setup

Use the WebUI Server Room Workload panel and click **Setup Dependencies** after
connecting machines. Setup requires passwordless `sudo apt` on the remote nodes
and installs/checks:

```text
build-essential openssl pigz xz-utils make coreutils python3
```

The setup flow also SCPs the local workload runner files to each remote
machine's configured `workdir`, so newly edited runner code can be used before a
git pull has happened on the remote.

## Workload mix

Generated scenarios use CPU-only templates:

| Template | Behavior |
| --- | --- |
| `crypto` | Repeated `openssl speed` SHA work, with Python hashing fallback. |
| `compress` | Compresses temporary random data with `pigz` or `xz`. |
| `compile` | Generates a temporary C project and repeatedly rebuilds it with `gcc`/`make`. |
| `python-cpu` | Pure Python multiprocessing hash loops. |

Each scenario job chooses a workload type, start delay, duration, and worker
count. CPU utilization is not closed-loop controlled; different utilization
comes from the selected template and number of workers.

## Runtime behavior

Starting a scenario launches one remote process group per machine:

```text
nohup setsid python3 -m workloads.runner run-job ...
```

Logs are written to `/tmp/burner_workload_<job_id>.log`. At the configured end
time, the runner exits and cleans its temporary directory. Manual stop sends
`SIGTERM` to the process group, waits up to five seconds, then sends `SIGKILL`.

## Scenario generation

The WebUI can generate a reproducible scenario from:

- target connected machines;
- seed;
- total window seconds;
- min/max duration seconds;
- min/max worker count.

The generated JSON is saved under:

```text
UI/scenarios/<name>.json
```

That file is reusable and can be inspected or edited manually if needed.

