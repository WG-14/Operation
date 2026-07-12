#!/usr/bin/env bash
set -euo pipefail

export NTFY_TOPIC=operation-dnjsckd5025

cd ~/work/operation
./scripts/run_codex_pipeline.sh
