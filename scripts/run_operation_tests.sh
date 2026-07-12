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
  tests/test_exchange_removal_contract.py
  tests/test_operation_cli_surface.py
  tests/test_offline_paper_runtime.py
  tests/test_config_live_db_path_guard.py
  tests/test_paths.py
  tests/test_run_lock.py
  tests/test_accounting_projection.py
  tests/test_fill_dedupe.py
  tests/test_recovery.py
  tests/test_execution_planner.py
  tests/test_execution_quality.py
  tests/test_lot_native_contract.py
  tests/test_deploy_systemd_units.py
  tests/test_operation_test_inventory.py
  tests/operator/test_reconcile_recovery.py
)

operation_pytest_setup_workspace "operation"
status=0
trap 'status=$?; operation_pytest_cleanup_workspace "$status"; exit "$status"' EXIT

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
operation_pytest_sanitize_unsafe_env "operation safety pytest runner"
operation_pytest_mark_pytest_started
uv run pytest -q "${OPERATION_TESTS[@]}"
