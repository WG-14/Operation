from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SMOKE_BACKTEST_WARNING = (
    "This is a smoke backtest only. It must not be used as evidence for strategy promotion, "
    "approved profiles, live readiness, or capital allocation."
)

ROOT_BACKTEST_REFUSAL = {
    "diagnostic_only": True,
    "non_promotable": True,
    "promotion_grade": False,
    "evidence_scope": "smoke_only_not_manifest_backed",
    "standalone_backtest_not_full_validation": True,
    "reason_code": "standalone_backtest_not_full_validation",
    "operator_next_action": "use_manifest_backed_research_validation",
    "promotion_command": "uv run bithumb-bot research-validate --manifest <path>",
    "diagnostic_command": "uv run bithumb-bot research-backtest --manifest <path>",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed compatibility wrapper. Promotion-grade validation must use "
            "`uv run bithumb-bot research-validate --manifest <path>`."
        )
    )
    parser.add_argument(
        "--diagnostic-smoke-only",
        action="store_true",
        help="Explicitly run the non-promotable smoke backtest implementation.",
    )
    args, remaining = parser.parse_known_args(argv)
    if not args.diagnostic_smoke_only:
        print(f"[SMOKE-BACKTEST REFUSED] {SMOKE_BACKTEST_WARNING}", file=sys.stderr)
        print(
            "diagnostic_only=true non_promotable=true promotion_grade=false "
            "evidence_scope=smoke_only_not_manifest_backed "
            "standalone_backtest_not_full_validation=true",
            file=sys.stderr,
        )
        print(
            "reason_code=standalone_backtest_not_full_validation "
            "operator_next_action=use_manifest_backed_research_validation "
            "promotion_command='uv run bithumb-bot research-validate --manifest <path>' "
            "diagnostic_command='uv run bithumb-bot research-backtest --manifest <path>'",
            file=sys.stderr,
        )
        return 2
    from bithumb_bot.smoke_backtest import main as smoke_main

    return smoke_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
