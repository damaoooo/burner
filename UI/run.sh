#!/usr/bin/env bash
set -euo pipefail

UI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
CONDA_ENV="${CONDA_ENV:-ReLL}"

cd "${UI_ROOT}/backend"
if ! conda run -n "${CONDA_ENV}" python -c "import asyncssh, fastapi, pydantic, uvicorn" >/dev/null 2>&1
then
  echo "Installing backend dependencies into conda env '${CONDA_ENV}'..."
  conda run -n "${CONDA_ENV}" python -m pip install -r requirements.txt
fi

cd "${UI_ROOT}/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi
npm run build

cd "${UI_ROOT}/backend"
exec conda run --no-capture-output -n "${CONDA_ENV}" \
  python -m uvicorn main:app --host "${HOST}" --port "${PORT}"
