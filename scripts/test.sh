#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT}"
if command -v pytest >/dev/null 2>&1; then
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" pytest tests/
elif command -v conda >/dev/null 2>&1; then
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" conda run -n ReLL python -m pytest tests/
else
  PYTHONPATH="${ROOT}:${PYTHONPATH:-}" python3 -m pytest tests/
fi
