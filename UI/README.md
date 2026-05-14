# Burner WebUI

## Single-Port Startup

Make sure the Conda environment `ReLL` exists and `node` / `npm` are available:

```bash
bash UI/run.sh
```

Default URL:

```text
http://localhost:8000
```

The frontend, `/api/*`, and `/ws` are all served by the same FastAPI process.

Optional environment variables:

```bash
BURNER_UI_PORT=9000 BURNER_UI_HOST=127.0.0.1 BURNER_CONDA_ENV=ReLL bash UI/run.sh
```

## Development Mode

For frontend hot reload, run the backend and frontend separately:

```bash
cd UI/backend
conda run --no-capture-output -n ReLL python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
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
git pull --recurse-submodules
```

It then SCPs the local patched source/build files to the remote workdir and rebuilds:

```bash
BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_lookbusy.sh
BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_gpu_burn.sh  # only when GPU hardware is detected
```

Burn and update actions are blocked while this rebuild is running. A sampling time is considered applied only after every target machine rebuilds successfully.
