#!/usr/bin/env bash

# 실패한 테스트가 있어도 다음 진단 명령을 계속 실행하기 위해 set -e는 쓰지 않음
set -u
set -o pipefail

run() {
  local title="$1"
  shift

  echo
  echo "============================================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $title"
  echo "COMMAND: $*"
  echo "============================================================"

  local start end status
  start=$(date +%s)

  "$@"
  status=$?

  end=$(date +%s)

  echo
  echo "---- RESULT: exit_code=$status elapsed=$((end - start))s ----"

  # 실패해도 다음 명령 계속 실행
  return 0
}

run "fast suite excluding slow/memory tests" \
  uv run pytest -q -m "not slow_research and not slow_integration and not memory_sensitive"

run "slow_research or memory_sensitive durations" \
  uv run pytest -q -m "slow_research or memory_sensitive" --durations=50 --durations-min=0

run "research backtest reproducibility durations" \
  uv run pytest -q tests/test_research_backtest_reproducibility.py --durations=50 --durations-min=0

run "research walk forward durations" \
  uv run pytest -q tests/test_research_walk_forward.py --durations=20 --durations-min=0

run "collect count: slow_research" \
  bash -lc 'uv run pytest --collect-only -q -m "slow_research" | awk "/::/ {n++} END {print n+0}"'

run "collect count: memory_sensitive" \
  bash -lc 'uv run pytest --collect-only -q -m "memory_sensitive" | awk "/::/ {n++} END {print n+0}"'

run "collect count: fast suite excluding slow/memory" \
  bash -lc 'uv run pytest --collect-only -q -m "not slow_research and not slow_integration and not memory_sensitive" | awk "/::/ {n++} END {print n+0}"'

run "cProfile: stress order independence test" \
  uv run python -m cProfile -o /tmp/stress_order.prof -m pytest -q \
  tests/test_research_backtest_reproducibility.py::test_stress_report_is_candidate_order_independent

run "print cProfile top cumulative time" \
  uv run python - <<'PY'
import pstats

p = pstats.Stats("/tmp/stress_order.prof")
p.strip_dirs().sort_stats("cumtime").print_stats(40)
PY

echo
echo "============================================================"
echo "Manual inspection checklist"
echo "============================================================"
echo "- SQLite insert/load"
echo "- dataset quality report"
echo "- strategy loop"
echo "- hash/content payload"
echo "- JSON artifact write"
echo "- audit trace write"
echo "- parallel executor overhead"

run "collect all tests" \
  uv run pytest --collect-only -q

run "full suite durations" \
  uv run pytest -q --durations=50 --durations-min=0

echo
echo "DONE: $(date '+%Y-%m-%d %H:%M:%S')"
