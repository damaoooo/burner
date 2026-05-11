#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_BURN_DIR="${ROOT}/third_party/gpu-burn"

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
make

echo "Built ${GPU_BURN_DIR}/gpu_burn"
