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

# Curated P0/P1 Operation safety gate. Performance-only suites are intentionally excluded.
OPERATION_TESTS=(
  tests/test_operation_research_boundary.py
  tests/test_operation_approval.py
  tests/test_operation_runtime_contract.py
  tests/test_operation_execution_calibration.py
  tests/test_operation_cli_surface.py
  tests/test_config_contract.py
  tests/test_paper_execute_harmless_dust.py
  tests/test_live_dry_run_state_isolation.py
  tests/test_operator_smoke_preflight.py
  tests/test_run_lock.py
  tests/test_fill_dedupe.py
  tests/test_order_submit_hardening.py
  tests/test_recovery_restart_regression.py
  tests/operator/test_reconcile_recovery.py
)

operation_pytest_setup_workspace "operation"
status=0
trap 'status=$?; operation_pytest_cleanup_workspace "$status"; exit "$status"' EXIT

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
operation_pytest_sanitize_unsafe_env "operation safety pytest runner"
operation_pytest_mark_pytest_started
uv run pytest -q "${OPERATION_TESTS[@]}"
