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

export OPERATION_TEST_TIER=fast
export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
duration_log="$(mktemp "${TMPDIR:-/tmp}/operation-fast-pytest-durations.XXXXXX.log")"

operation_pytest_setup_workspace "fast"
status=0
trap 'status=$?; rm -f "$duration_log"; operation_pytest_cleanup_workspace "$status"; exit "$status"' EXIT

operation_pytest_sanitize_unsafe_env "fast PR pytest runner"

operation_pytest_mark_pytest_started
uv run pytest -q \
  --durations=50 \
  --durations-min=0.25 | tee "$duration_log"
uv run python scripts/check_fast_test_durations.py "$duration_log" --max-seconds 10
