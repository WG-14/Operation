from __future__ import annotations

from bithumb_bot.h74_probe_acceptance import evaluate_h74_execution_path_probe_acceptance


def _full() -> dict[str, object]:
    return {
        "buy_decision": True,
        "buy_execution_plan": True,
        "buy_order_event": True,
        "buy_fill": True,
        "open_lot": True,
        "sell_decision": True,
        "sell_execution_plan": True,
        "sell_order_event": True,
        "sell_fill": True,
        "closed_trade_lifecycle": True,
        "final_flat_or_documented_dust": True,
    }


def test_no_window_probe_pass_requires_buy_and_sell_fills() -> None:
    result = evaluate_h74_execution_path_probe_acceptance(_full())
    assert result["execution_path_probe_status"] == "PASS"


def test_no_window_probe_without_sell_fill_is_incomplete() -> None:
    evidence = _full()
    evidence["sell_fill"] = False
    result = evaluate_h74_execution_path_probe_acceptance(evidence)
    assert result["execution_path_probe_status"] != "PASS"
    assert "sell_fill" in result["missing_evidence"]


def test_no_window_probe_never_sets_research_equivalence_true() -> None:
    result = evaluate_h74_execution_path_probe_acceptance(_full())
    assert result["research_equivalence"] is False
    assert result["research_equivalence_status"] == "NOT_APPLICABLE"


def test_no_window_probe_never_sets_production_approval_true() -> None:
    result = evaluate_h74_execution_path_probe_acceptance(_full())
    assert result["production_approval"] is False
    assert result["promotion_grade"] is False
