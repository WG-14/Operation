from __future__ import annotations

from pathlib import Path

import backtest
from bithumb_bot import smoke_backtest


def test_root_backtest_is_explicitly_marked_smoke_only() -> None:
    assert (
        "This is a smoke backtest only. It must not be used as evidence for strategy promotion, "
        "approved profiles, live readiness, or capital allocation."
    ) == smoke_backtest.SMOKE_BACKTEST_WARNING


def test_root_backtest_is_fail_closed_wrapper_not_smoke_implementation() -> None:
    assert backtest.ROOT_BACKTEST_REFUSAL["promotion_grade"] is False
    assert backtest.ROOT_BACKTEST_REFUSAL["evidence_scope"] == "smoke_only_not_manifest_backed"
    assert not hasattr(backtest, "do_buy")
    assert not hasattr(backtest, "backtest")


def test_docs_say_smoke_backtests_cannot_justify_promotion_or_live_readiness() -> None:
    docs = (Path("docs/research-validation.md").read_text(encoding="utf-8") + "\n" + Path("README.md").read_text(encoding="utf-8"))

    assert "smoke backtest" in docs.lower()
    assert "must not be used as evidence for strategy promotion" in docs
    assert "live readiness" in docs
