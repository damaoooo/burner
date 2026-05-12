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
PORT=9000 HOST=127.0.0.1 CONDA_ENV=ReLL bash UI/run.sh
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
