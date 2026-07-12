#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"

OUT_DIR="${1:-${SCRIPT_DIR}/rendered}"
mkdir -p "${OUT_DIR}"

OPERATION_BOT_ROOT="${OPERATION_BOT_ROOT:-${REPO_ROOT}}"
OPERATION_ENV_FILE_LIVE="${OPERATION_ENV_FILE_LIVE:-/etc/operation/operation.live.env}"
OPERATION_ENV_FILE_PAPER="${OPERATION_ENV_FILE_PAPER:-/etc/operation/operation.paper.env}"
OPERATION_ENV_ROOT="${OPERATION_ENV_ROOT:-/var/lib/operation/env}"
OPERATION_RUN_ROOT="${OPERATION_RUN_ROOT:-/var/lib/operation/run}"
OPERATION_DATA_ROOT="${OPERATION_DATA_ROOT:-/var/lib/operation/data}"
OPERATION_LOG_ROOT="${OPERATION_LOG_ROOT:-/var/lib/operation/logs}"
OPERATION_BACKUP_ROOT="${OPERATION_BACKUP_ROOT:-/var/lib/operation/backup}"
OPERATION_RUN_USER="${OPERATION_RUN_USER:-$(id -un)}"
DEFAULT_UV_BIN="$(command -v uv || true)"
OPERATION_UV_BIN="${OPERATION_UV_BIN:-${DEFAULT_UV_BIN:-uv}}"

for unit in "${SCRIPT_DIR}"/*.service "${SCRIPT_DIR}"/*.timer; do
  target="${OUT_DIR}/$(basename "${unit}")"
  sed \
    -e "s|@OPERATION_BOT_ROOT@|${OPERATION_BOT_ROOT}|g" \
    -e "s|@OPERATION_ENV_FILE_LIVE@|${OPERATION_ENV_FILE_LIVE}|g" \
    -e "s|@OPERATION_ENV_FILE_PAPER@|${OPERATION_ENV_FILE_PAPER}|g" \
    -e "s|@OPERATION_ENV_ROOT@|${OPERATION_ENV_ROOT}|g" \
    -e "s|@OPERATION_RUN_ROOT@|${OPERATION_RUN_ROOT}|g" \
    -e "s|@OPERATION_DATA_ROOT@|${OPERATION_DATA_ROOT}|g" \
    -e "s|@OPERATION_LOG_ROOT@|${OPERATION_LOG_ROOT}|g" \
    -e "s|@OPERATION_BACKUP_ROOT@|${OPERATION_BACKUP_ROOT}|g" \
    -e "s|@OPERATION_UV_BIN@|${OPERATION_UV_BIN}|g" \
    -e "s|@OPERATION_RUN_USER@|${OPERATION_RUN_USER}|g" \
    "${unit}" > "${target}"
done

echo "Rendered systemd units to ${OUT_DIR}"
