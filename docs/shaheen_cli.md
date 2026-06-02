# Shaheen CLI

Use `scripts/shaheen_cli.py` when large allocations make the WebUI too heavy. The CLI uses the same SLURM controller and shared filesystem commands as the WebUI, but it does not render or poll node cards.

## One-command Run

Run a synchronized CPU burn on `N` Shaheen nodes:

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py run \
  -N 2000 \
  --time 00:15:00 \
  --poll-ms 100 \
  --sample-ms 200 \
  --duration 10m \
  --period 1s \
  --waveform full
```

This submits the SLURM allocation, waits until all workers are ready, then writes one synchronized start command. Immediate mode still uses a near-future UTC start timestamp after the ready barrier.

## Separate Steps

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py submit -N 2000 --time 00:15:00
conda run --no-capture-output -n burner python scripts/shaheen_cli.py wait-ready --timeout 1800 --interval 5
conda run --no-capture-output -n burner python scripts/shaheen_cli.py start --duration 10m --period 1s --waveform full
```

Check status:

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py status
```

Stop burn but keep nodes:

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py stop
```

Release nodes:

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py release
```

Export the latest load CSV:

```bash
conda run --no-capture-output -n burner python scripts/shaheen_cli.py export-load \
  -o /scratch/zhoul0e/latest-burn-load.csv
```

## Notes

- Default waveform is `full`, which is `100%` burn from `tests/fixtures/full.csv`.
- GPU burn is not exposed; Shaheen CLI starts CPU-only burn on every ready node.
- `wait-ready` prints only aggregate progress like `ready 1980/2000`, so it is suitable for large allocations.
- `start` requires every allocated worker to be ready and enabled. This preserves the same synchronization barrier as the WebUI.
