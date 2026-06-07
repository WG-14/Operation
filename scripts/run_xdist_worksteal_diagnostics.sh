#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

iterations="${PYTEST_WORKSTEAL_DIAGNOSTIC_ITERATIONS:-3}"
export PYTEST_XDIST_WORKERS="${PYTEST_XDIST_WORKERS:-8}"
export PYTEST_XDIST_DIST="${PYTEST_XDIST_DIST:-worksteal}"

for ((iteration = 1; iteration <= iterations; iteration += 1)); do
  echo "[XDIST-WORKSTEAL-DIAGNOSTIC] iteration=${iteration}/${iterations} workers=${PYTEST_XDIST_WORKERS} dist=${PYTEST_XDIST_DIST}"
  ./scripts/run_full_pytest_tests.sh
done
