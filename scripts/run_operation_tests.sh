#!/usr/bin/env bash
set -euo pipefail

if [[ "${BITHUMB_CODEX_BLOCK_BROAD_TEST_RUNNERS:-0}" == "1" ]]; then
  echo "[CODEX-BROAD-RUNNER-GUARD] Codex ${BITHUMB_CODEX_MODE:-session} must not run ${BASH_SOURCE[0]}." >&2
  echo "[CODEX-BROAD-RUNNER-GUARD] Run only focused validation directly related to the patch or failure packet." >&2
  exit 126
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/pytest_workspace.sh"

# Curated P0/P1 operation safety gate. Research validation and performance suites
# are intentionally excluded; their ownership remains with dedicated research runners.
OPERATION_TESTS=(
  tests/test_operation_research_import_boundary.py
  tests/test_operation_cli_surface.py
  tests/test_operation_notification_policy.py
  tests/test_artifact_hashing.py
  tests/test_runtime_architecture_graph.py
  tests/test_runtime_authority_boundaries.py
  tests/test_live_preflight.py
  tests/test_run_lock.py
  tests/test_fill_dedupe.py
  tests/test_order_submit_hardening.py
  tests/test_execution_service_contract.py
  tests/test_recovery.py
  tests/test_recovery_restart_regression.py
  tests/test_lot_native_contract.py
)

bithumb_pytest_setup_workspace "operation"
status=0
trap 'status=$?; bithumb_pytest_cleanup_workspace "$status"; exit "$status"' EXIT

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
bithumb_pytest_sanitize_unsafe_env "operation safety pytest runner"
bithumb_pytest_mark_pytest_started
uv run pytest -q "${OPERATION_TESTS[@]}"
