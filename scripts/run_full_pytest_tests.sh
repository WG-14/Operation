#!/usr/bin/env bash
set -euo pipefail

if [[ "${OPERATION_CODEX_BLOCK_BROAD_TEST_RUNNERS:-0}" == "1" ]]; then
  echo "[CODEX-BROAD-RUNNER-GUARD] Codex ${OPERATION_CODEX_MODE:-session} must not run ${BASH_SOURCE[0]}." >&2
  echo "[CODEX-BROAD-RUNNER-GUARD] Run only focused validation directly related to the patch or failure packet." >&2
  exit 126
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/pytest_workspace.sh"

operation_pytest_setup_workspace "full"
export OPERATION_PYTEST_SUMMARY_ON_SUCCESS=1
status=0
trap 'status=$?; operation_pytest_cleanup_workspace "$status"; exit "$status"' EXIT

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"

operation_pytest_sanitize_unsafe_env "full pytest runner"

operation_pytest_mark_pytest_started
pytest_args=(-q)
if [[ -n "${PYTEST_XDIST_WORKERS:-}" && "${PYTEST_XDIST_WORKERS:-0}" != "0" ]]; then
  pytest_dist="${PYTEST_XDIST_DIST:-worksteal}"
  echo "[PYTEST-XDIST] workers=${PYTEST_XDIST_WORKERS} dist=${pytest_dist}"
  pytest_args+=(-n "$PYTEST_XDIST_WORKERS" --dist="${pytest_dist}")
fi
uv run pytest "${pytest_args[@]}"
