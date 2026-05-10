#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOOKBUSY_DIR="${ROOT}/third_party/lookbusy"

cd "${LOOKBUSY_DIR}"

if ! bash ./configure; then
  echo "lookbusy configure failed." >&2
  exit 1
fi

if ! make lookbusy; then
  echo "lookbusy make failed; falling back to direct gcc build." >&2
  if [[ ! -f config.h ]]; then
    echo "config.h is missing; cannot use direct gcc fallback." >&2
    exit 1
  fi
  gcc -DHAVE_CONFIG_H -I. -O2 -o lookbusy lb.c -lm
fi

echo "Built ${LOOKBUSY_DIR}/lookbusy"
