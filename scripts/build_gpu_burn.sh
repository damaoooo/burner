#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_BURN_DIR="${ROOT}/third_party/gpu-burn"

if ! command -v nvcc >/dev/null 2>&1 && [[ ! -x /usr/local/cuda/bin/nvcc ]] && [[ ! -x /usr/bin/nvcc ]]; then
  echo "CUDA nvcc was not found; install CUDA or set CUDAPATH before building gpu-burn." >&2
  exit 1
fi

cd "${GPU_BURN_DIR}"
make

echo "Built ${GPU_BURN_DIR}/gpu_burn"
