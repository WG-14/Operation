from __future__ import annotations

import pytest

from bithumb_bot.execution_service import ExecutionSubmitPlan
from bithumb_bot.research.backtest_kernel import execution_submit_plan_to_research_request


def _plan(
    *,
    side: str,
    qty: float | None,
    notional_krw: float | None,
    submit_expected: bool = True,
    block_reason: str = "none",
) -> ExecutionSubmitPlan:
    return ExecutionSubmitPlan(
        side=side,
        source="research_backtest",
        authority="strategy_execution_intent",
        final_action="ENTER_STRATEGY_POSITION" if side == "BUY" else "EXIT_STRATEGY_POSITION",
        qty=qty,
        notional_krw=notional_krw,
        target_exposure_krw=notional_krw,
        current_effective_exposure_krw=0.0,
        delta_krw=notional_krw,
        submit_expected=submit_expected,
        pre_submit_proof_status="not_required",
        block_reason=block_reason,
        idempotency_key=None,
    )


def _request(plan: ExecutionSubmitPlan):
    return execution_submit_plan_to_research_request(
        submit_plan=plan,
        signal_ts=100,
        decision_ts=200,
        reference_price=10.0,
        fee_rate=0.001,
        timing_fields={"submit_ts_assumption": 201},
        depth_fields={"depth_available": False},
    )


def test_buy_submit_plan_produces_request_from_plan_notional() -> None:
    request = _request(_plan(side="BUY", qty=999.0, notional_krw=12345.0))

    assert request is not None
    assert request.side == "BUY"
    assert request.requested_notional == 12345.0
    assert request.requested_qty == 999.0


def test_sell_submit_plan_produces_request_from_plan_qty() -> None:
    request = _request(_plan(side="SELL", qty=0.25, notional_krw=2500.0))

    assert request is not None
    assert request.side == "SELL"
    assert request.requested_qty == 0.25
    assert request.requested_notional == 2500.0


def test_submit_not_expected_produces_no_research_fill_request() -> None:
    request = _request(
        _plan(
            side="BUY",
            qty=None,
            notional_krw=None,
            submit_expected=False,
            block_reason="research_zero_buy_notional",
        )
    )

    assert request is None


def test_malformed_submit_plan_fails_closed_before_research_request() -> None:
    with pytest.raises(ValueError, match="research_submit_plan_not_typed"):
        execution_submit_plan_to_research_request(
            submit_plan={"side": "BUY"},  # type: ignore[arg-type]
            signal_ts=100,
            decision_ts=200,
            reference_price=10.0,
            fee_rate=0.001,
            timing_fields={},
            depth_fields={},
        )


def test_direct_cash_fraction_is_not_request_authority_when_plan_exists() -> None:
    cash = 1_000_000.0
    legacy_buy_fraction = 0.5
    request = _request(_plan(side="BUY", qty=None, notional_krw=12_000.0))

    assert request is not None
    assert request.requested_notional == 12_000.0
    assert request.requested_notional != cash * legacy_buy_fraction
