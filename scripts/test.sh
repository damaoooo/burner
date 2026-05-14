#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT}"
CONDA_TEST_ENV="${BURNER_TEST_CONDA_ENV:-burner}"

if command -v pytest >/dev/null 2>&1; then
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" pytest tests/
elif command -v conda >/dev/null 2>&1; then
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" conda run -n "${CONDA_TEST_ENV}" python -m pytest tests/
else
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" python3 -m pytest tests/
fi
