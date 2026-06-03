#!/usr/bin/env bash
set -euo pipefail

uv run python scripts/check_research_test_policy.py
uv run pytest -q \
  -m "not research_e2e and not audit_e2e and not walk_forward_e2e and not parallel_e2e and not nightly and not slow_research and not memory_sensitive" \
  --durations=50 \
  --durations-min=0.25
