from __future__ import annotations

import pytest

from operation.config import settings
from operation.execution_service import build_execution_decision_summary


def _readiness(**overrides: object) -> dict[str, object]:
    readiness: dict[str, object] = {
        "broker_position_evidence": {"broker_qty_known": True, "broker_qty": 0.0, "balance_source_stale": False},
        "projection_converged": True,
        "projection_convergence": {"converged": True},
        "broker_portfolio_converged": True,
        "open_order_count": 0,
        "unresolved_open_order_count": 0,
        "recovery_required_count": 0,
        "submit_unknown_count": 0,
        "accounting_projection_ok": True,
        "cash_available": 1_000_000.0,
        "min_qty": 0.0001,
        "qty_step": 0.0001,
        "min_notional_krw": 5_000.0,
    }
    readiness.update(overrides)
    return readiness


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"unresolved_open_order_count": 1}, "unresolved_open_order_count_nonzero"),
        ({"projection_converged": False}, "projection_not_converged"),
        ({"accounting_projection_ok": False}, "accounting_projection_not_ok"),
    ],
)
def test_target_delta_readiness_fail_closed_for_active_safety_blockers(
    override: dict[str, object], reason: str
) -> None:
    old = {name: getattr(settings, name) for name in ("MODE", "EXECUTION_ENGINE", "LIVE_DRY_RUN", "LIVE_REAL_ORDER_ARMED")}
    try:
        object.__setattr__(settings, "MODE", "live")
        object.__setattr__(settings, "EXECUTION_ENGINE", "target_delta")
        object.__setattr__(settings, "LIVE_DRY_RUN", False)
        object.__setattr__(settings, "LIVE_REAL_ORDER_ARMED", True)
        summary = build_execution_decision_summary(
            decision_context={"raw_signal": "BUY", "final_signal": "BUY", "market_price": 100_000_000.0},
            readiness_payload=_readiness(**override),
            raw_signal="BUY",
            final_signal="BUY",
            final_reason="sma_cross",
        )
    finally:
        for name, value in old.items():
            object.__setattr__(settings, name, value)
    assert summary.target_submit_plan is not None
    assert summary.target_submit_plan.block_reason == reason
    assert summary.target_submit_plan.submit_expected is False
