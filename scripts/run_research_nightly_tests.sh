#!/usr/bin/env bash
set -euo pipefail

uv run python scripts/check_research_test_policy.py
uv run pytest -q \
  -m "research_e2e or audit_e2e or walk_forward_e2e or parallel_e2e or nightly or slow_research" \
  --durations=100 \
  --durations-min=0.25
