#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_BURN_DIR="${ROOT}/third_party/gpu-burn"
CONTROL_INTERVAL_MS="${BURNER_CONTROL_INTERVAL_MS:-100}"

if [[ ! "${CONTROL_INTERVAL_MS}" =~ ^[0-9]+$ ]] || (( CONTROL_INTERVAL_MS < 10 || CONTROL_INTERVAL_MS > 1000 )); then
  echo "BURNER_CONTROL_INTERVAL_MS must be an integer between 10 and 1000." >&2
  exit 2
fi

if [[ ! -f "${GPU_BURN_DIR}/Makefile" ]]; then
  echo "gpu-burn Makefile was not found at ${GPU_BURN_DIR}/Makefile." >&2
  echo "Initialize third-party submodules first:" >&2
  echo "  git submodule update --init --recursive" >&2
  exit 1
fi

if ! command -v nvcc >/dev/null 2>&1 && [[ ! -x /usr/local/cuda/bin/nvcc ]] && [[ ! -x /usr/bin/nvcc ]]; then
  echo "CUDA nvcc was not found; install CUDA or set CUDAPATH before building gpu-burn." >&2
  exit 1
fi

cd "${GPU_BURN_DIR}"
echo "Building gpu_burn with BURNER_CONTROL_INTERVAL_MS=${CONTROL_INTERVAL_MS}"
CFLAGS="${CFLAGS:-} -DBURNER_CONTROL_INTERVAL_MS=${CONTROL_INTERVAL_MS}" make -B gpu_burn

echo "Built ${GPU_BURN_DIR}/gpu_burn"
