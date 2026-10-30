#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# -march=native: the binary is built on the machine that runs it, so the FMA
# kernel uses the widest vectors that CPU has (AVX-512 on Zen 4).
gcc -O2 -march=native -pthread -Wall -Wextra -o "${ROOT}/native/cpu_pwm" "${ROOT}/native/cpu_pwm.c"

echo "Built ${ROOT}/native/cpu_pwm"
