#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOOKBUSY_DIR="${ROOT}/third_party/lookbusy"
CONTROL_INTERVAL_MS="${BURNER_CONTROL_INTERVAL_MS:-100}"

if [[ ! "${CONTROL_INTERVAL_MS}" =~ ^[0-9]+$ ]] || (( CONTROL_INTERVAL_MS < 10 || CONTROL_INTERVAL_MS > 1000 )); then
  echo "BURNER_CONTROL_INTERVAL_MS must be an integer between 10 and 1000." >&2
  exit 2
fi

INTERVAL_CFLAGS="-DBURNER_CONTROL_INTERVAL_MS=${CONTROL_INTERVAL_MS}"

cd "${LOOKBUSY_DIR}"

if ! bash ./configure; then
  echo "lookbusy configure failed." >&2
  exit 1
fi

echo "Building lookbusy with BURNER_CONTROL_INTERVAL_MS=${CONTROL_INTERVAL_MS}"

if ! CFLAGS="${CFLAGS:-} ${INTERVAL_CFLAGS}" make -B lookbusy; then
  echo "lookbusy make failed; falling back to direct gcc build." >&2
  if [[ ! -f config.h ]]; then
    echo "config.h is missing; cannot use direct gcc fallback." >&2
    exit 1
  fi
  gcc -DHAVE_CONFIG_H -I. -O2 "${INTERVAL_CFLAGS}" -o lookbusy lb.c -lm
fi

echo "Built ${LOOKBUSY_DIR}/lookbusy"
