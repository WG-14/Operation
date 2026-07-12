#!/usr/bin/env bash
set -euo pipefail

OPERATION_PYTEST_WORKSPACE=""
OPERATION_PYTEST_WORKSPACE_PARENT=""
OPERATION_PYTEST_SUITE=""
OPERATION_PYTEST_PREFLIGHT_STAGE=""
OPERATION_PYTEST_STARTED=0

OPERATION_PYTEST_BROKER_PRIVATE_ENV_KEYS=(
)

OPERATION_PYTEST_EXTERNAL_NOTIFICATION_ENV_KEYS=(
  NTFY_TOPIC
  NOTIFIER_WEBHOOK_URL
  SLACK_WEBHOOK_URL
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
)

operation_pytest_repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

operation_pytest_resolve_path() {
  local path="$1"
  python3 - "$path" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

operation_pytest_refuse_unsafe_path() {
  local target="$1"
  local repo_root="$2"
  if [[ -z "$target" || "$target" == "/" ]]; then
    echo "[PYTEST-WORKSPACE] refusing unsafe cleanup target: ${target:-<empty>}" >&2
    return 1
  fi
  python3 - "$target" "$repo_root" <<'PY'
from pathlib import Path
import sys
target = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
if target == repo or repo in target.parents:
    print(f"[PYTEST-WORKSPACE] refusing repo-local cleanup target: {target}", file=sys.stderr)
    raise SystemExit(1)
PY
}

operation_pytest_setup_workspace() {
  local suite_name="${1:?suite name required}"
  local repo_root
  repo_root="$(operation_pytest_repo_root)"
  local workspace_root="${OPERATION_PYTEST_WORKSPACE_ROOT:-/tmp/operation-pytest-${USER:-user}}"
  workspace_root="$(operation_pytest_resolve_path "$workspace_root")"
  operation_pytest_refuse_unsafe_path "$workspace_root" "$repo_root"

  local run_id="${OPERATION_PYTEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  OPERATION_PYTEST_WORKSPACE_PARENT="$workspace_root"
  OPERATION_PYTEST_WORKSPACE="$workspace_root/$suite_name/$run_id"
  OPERATION_PYTEST_SUITE="$suite_name"
  export OPERATION_PYTEST_RUN_ID="$run_id"
  export OPERATION_PYTEST_SUITE
  export PYTEST_DEBUG_TEMPROOT="$OPERATION_PYTEST_WORKSPACE/pytest-debug"
  mkdir -p "$PYTEST_DEBUG_TEMPROOT"
  echo "[PYTEST-WORKSPACE] suite=$suite_name run_id=$run_id"
  echo "[PYTEST-WORKSPACE] root=$OPERATION_PYTEST_WORKSPACE"
  echo "[PYTEST-WORKSPACE] PYTEST_DEBUG_TEMPROOT=$PYTEST_DEBUG_TEMPROOT"
}

operation_pytest_sanitize_unsafe_env() {
  local runner_name="${1:-pytest runner}"
  local key

  for key in "${OPERATION_PYTEST_BROKER_PRIVATE_ENV_KEYS[@]}"; do
    unset "$key"
  done

  if [[ "${OPERATION_PYTEST_ALLOW_EXTERNAL_NOTIFICATIONS:-0}" == "1" ]]; then
    echo "[PYTEST-SAFETY] broker-private env disabled for ${runner_name}; external notification env allowed by explicit opt-in"
    return 0
  fi

  export NOTIFIER_ENABLED=false
  for key in "${OPERATION_PYTEST_EXTERNAL_NOTIFICATION_ENV_KEYS[@]}"; do
    unset "$key"
  done
  echo "[PYTEST-SAFETY] unsafe inherited env disabled for ${runner_name}"
}

operation_pytest_preflight_report_path() {
  if [[ -z "${OPERATION_PYTEST_WORKSPACE:-}" ]]; then
    return 1
  fi
  printf '%s\n' "$OPERATION_PYTEST_WORKSPACE/preflight_failure.json"
}

operation_pytest_workspace_size_bytes() {
  if [[ -z "${OPERATION_PYTEST_WORKSPACE:-}" || ! -d "$OPERATION_PYTEST_WORKSPACE" ]]; then
    printf '0\n'
    return 0
  fi
  du -sb "$OPERATION_PYTEST_WORKSPACE" 2>/dev/null | awk '{print $1}'
}

operation_pytest_record_preflight_failure() {
  local stage="${1:?preflight stage required}"
  local status="${2:-1}"
  local command="${3:-}"
  local workspace_size
  workspace_size="$(operation_pytest_workspace_size_bytes)"
  local report_path
  report_path="$(operation_pytest_preflight_report_path)"
  mkdir -p "$(dirname "$report_path")"
  python3 - "$report_path" "$OPERATION_PYTEST_SUITE" "$OPERATION_PYTEST_WORKSPACE" "$stage" "$status" "$command" "$workspace_size" <<'PY'
import json
from pathlib import Path
import sys

report_path = Path(sys.argv[1])
payload = {
    "suite": sys.argv[2],
    "workspace_root": sys.argv[3],
    "failed_stage": sys.argv[4],
    "pytest_started": False,
    "status": "preflight_failed",
    "reason": f"preflight stage failed before pytest: {sys.argv[4]}",
    "command": sys.argv[6],
    "exit_code": int(sys.argv[5]),
    "retained_workspace_size_bytes": int(sys.argv[7] or 0),
}
report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  echo "[PYTEST-PREFLIGHT] failed suite=$OPERATION_PYTEST_SUITE stage=$stage exit_code=$status"
  echo "[PYTEST-PREFLIGHT] pytest did not start"
  echo "[PYTEST-PREFLIGHT] workspace=$OPERATION_PYTEST_WORKSPACE retained_size_bytes=${workspace_size:-0}"
  echo "[PYTEST-PREFLIGHT] report=$report_path"
}

operation_pytest_run_preflight() {
  local stage="${1:?preflight stage required}"
  shift
  local status
  OPERATION_PYTEST_PREFLIGHT_STAGE="$stage"
  echo "[PYTEST-PREFLIGHT] start suite=$OPERATION_PYTEST_SUITE stage=$stage command=$*"
  if "$@"; then
    echo "[PYTEST-PREFLIGHT] ok suite=$OPERATION_PYTEST_SUITE stage=$stage"
    OPERATION_PYTEST_PREFLIGHT_STAGE=""
    return 0
  else
    status=$?
    operation_pytest_record_preflight_failure "$stage" "$status" "$*"
    OPERATION_PYTEST_PREFLIGHT_STAGE=""
    return "$status"
  fi
}

operation_pytest_mark_pytest_started() {
  OPERATION_PYTEST_STARTED=1
  export OPERATION_PYTEST_STARTED
  echo "[PYTEST-WORKSPACE] pytest_started=1 suite=$OPERATION_PYTEST_SUITE workspace=$OPERATION_PYTEST_WORKSPACE"
}

operation_pytest_workspace_summary() {
  if [[ -z "${OPERATION_PYTEST_WORKSPACE:-}" || ! -d "$OPERATION_PYTEST_WORKSPACE" ]]; then
    return 0
  fi
  local bytes
  bytes="$(du -sb "$OPERATION_PYTEST_WORKSPACE" 2>/dev/null | awk '{print $1}')"
  echo "[PYTEST-WORKSPACE] retained_size_bytes=${bytes:-0} path=$OPERATION_PYTEST_WORKSPACE"
  find "$OPERATION_PYTEST_WORKSPACE" -type f -printf '%s %p\n' 2>/dev/null \
    | sort -nr \
    | head -10 \
    | awk '{print "[PYTEST-WORKSPACE] large_file_bytes="$1" path="$2}'
}

operation_pytest_cleanup_workspace() {
  local status="${1:-0}"
  local repo_root
  repo_root="$(operation_pytest_repo_root)"
  if [[ -z "${OPERATION_PYTEST_WORKSPACE:-}" ]]; then
    return 0
  fi
  operation_pytest_refuse_unsafe_path "$OPERATION_PYTEST_WORKSPACE" "$repo_root"
  if [[ "${KEEP_OPERATION_TEST_ARTIFACTS:-0}" == "1" || "$status" != "0" ]]; then
    echo "[PYTEST-WORKSPACE] keeping workspace: $OPERATION_PYTEST_WORKSPACE"
    operation_pytest_workspace_summary
    return 0
  fi
  if [[ "${OPERATION_PYTEST_SUMMARY_ON_SUCCESS:-0}" == "1" ]]; then
    operation_pytest_workspace_summary
  fi
  rm -rf "$OPERATION_PYTEST_WORKSPACE"
  echo "[PYTEST-WORKSPACE] cleaned workspace: $OPERATION_PYTEST_WORKSPACE"
}
