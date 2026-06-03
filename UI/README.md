# Burner WebUI

## Shaheen Single-Port Startup

Make sure the Conda environment `burner` exists and `node` / `npm` are available. One command builds the frontend and starts the FastAPI backend that serves the frontend, `/api/*`, and `/ws` on the same port:

```bash
BURNER_UI_PORT=18080 bash UI/run.sh
```

Default URL:

```text
http://localhost:18080
```

If you do not set `BURNER_UI_PORT`, the script uses port `8000`.

In the SLURM allocation panel, `Sample / UI Refresh (ms)` defaults to `200`. Before all workers are ready, this controls how often the UI polls allocation readiness and the current machine page. After `Ready Nodes` reaches the requested node count, the WebUI stops high-frequency status polling and only refreshes node details when the page/session changes or a user action requires it.

For allocations above 50 nodes, the WebUI uses a compact cluster burn path. Start/stop/status events are represented as one aggregate cluster job instead of one websocket/HTTP job record per node, so a 2000-node allocation does not push 2000 job messages through the browser. The `start-all` backend path writes one shared command and returns one aggregate job; it does not build per-node burn requests.

Start synchronization uses a shared SLURM command file with one future UTC `start_at` timestamp. Workers poll the command file, spawn `burner --start <start_at>`, prewarm lookbusy at 0%, and only switch to the waveform at the shared timestamp. For large allocations the immediate-start lead time scales with node count; 2000 nodes default to a 15 second arming window. Set `BURNER_SLURM_START_LEAD_SECONDS` before starting the UI to force a larger lead time. Worker arming acknowledgements are written under the session `acks/<sequence>/` directory for post-run timing checks.

After a burn finishes, the cluster power chart is built from completed per-node CSV files. On large allocations, workers first copy local `/tmp` samples back to the shared session directory, then the backend streams those files into a downsampled cluster curve. The first chart load reads the completed CSV files once; repeated loads of the same completed sample set are served from an in-memory cache.

Optional environment variables:

```bash
BURNER_UI_PORT=9000 BURNER_UI_HOST=127.0.0.1 BURNER_CONDA_ENV=burner bash UI/run.sh
```

## Development Mode

For frontend hot reload, run the backend and frontend separately:

```bash
cd UI/backend
conda run --no-capture-output -n burner python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
cd UI/frontend
npm install
npm run dev
```

The Vite development frontend runs at `http://localhost:5173` and proxies `/api` and `/ws` to `localhost:8000`.

## Sampling Time Apply Flow

The UI exposes `Sampling Time (ms)` in the run parameters panel. Valid values are `10` through `1000`, with `100` as the default.

Clicking `Apply Sampling Time` targets all currently connected machines. For each target, the backend runs:

```bash
cd <remote workdir>
git reset --hard HEAD
git clean -fd
git submodule foreach --recursive 'git reset --hard HEAD && git clean -fd'
git pull --recurse-submodules
git submodule sync --recursive
git submodule update --init --recursive --force
```

It then SCPs the local patched source/build files to the remote workdir and rebuilds:

```bash
BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_lookbusy.sh
BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_gpu_burn.sh  # only when GPU hardware is detected
```

Burn and update actions are blocked while this rebuild is running. A sampling time is considered applied only after every target machine rebuilds successfully.

## Update Flow

The per-machine `Update` action uses the same submodule-safe repository preparation before building:

```bash
cd <remote workdir>
git reset --hard HEAD
git clean -fd
git submodule foreach --recursive 'git reset --hard HEAD && git clean -fd'
git pull --recurse-submodules
git submodule sync --recursive
git submodule update --init --recursive --force
```

It then rebuilds CPU support and, when GPU hardware is detected, GPU support.

## Burn Launch Path

The per-machine burn action starts `./burner` through the configured Conda environment, but it prefers the environment `bin/` directory directly instead of `conda run`. This avoids per-machine `conda run` startup overhead during realtime starts. If the environment path cannot be found, it falls back to `conda run`.

For delayed and scheduled starts, `./burner --start` prewarms selected backends at 0% load shortly before the planned start time. This reduces GPU initialization skew before the first non-zero load update.
