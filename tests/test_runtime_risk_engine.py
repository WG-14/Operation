from __future__ import annotations

import sqlite3

from bithumb_bot.risk import DailyLossEvaluation
from bithumb_bot.risk_contract import RiskPolicy, SubmitPlan
from bithumb_bot.runtime_risk_engine import RuntimeRiskEngineAdapter


def test_pre_submit_flat_buy_uses_broker_qty_zero_not_submit_qty(monkeypatch) -> None:
    def _daily_loss_state(*_args, **_kwargs) -> DailyLossEvaluation:
        return DailyLossEvaluation(
            blocked=False,
            reason="ok",
            reason_code="OK",
            decision="allow",
            evaluation_ts_ms=1_800_000_000_000,
            day_kst="2026-06-22",
            max_daily_loss_krw=50_000.0,
            start_equity=1_000_000.0,
            current_equity=1_000_000.0,
            loss_today=0.0,
            current_cash_krw=1_000_000.0,
            current_asset_qty=0.0,
            mark_price=100_000_000.0,
            mark_price_source="unit",
            details={"current_source": "broker_balance_snapshot"},
        )

    monkeypatch.setattr("bithumb_bot.runtime_risk_engine.evaluate_daily_loss_state", _daily_loss_state)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._latest_position_entry_price", lambda _conn: None)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._count_orders_today", lambda _conn, _ts: 0)
    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.collect_risky_order_state",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine._record_typed_decision_identity",
        lambda *_args, **_kwargs: None,
    )

    conn = sqlite3.connect(":memory:")
    try:
        decision = RuntimeRiskEngineAdapter(conn, policy=RiskPolicy(max_daily_loss_krw=50_000.0)).evaluate_pre_submit(
            plan=SubmitPlan(side="BUY", qty=0.0002, notional_krw=20_000.0, source="target_delta"),
            ts_ms=1_800_000_000_000,
            now_ms=1_800_000_000_000,
            cash=0.0,
            submit_qty=0.0002,
            current_asset_qty=None,
            price=100_000_000.0,
            broker=object(),
            evaluation_origin="live_real_submit_authority_pre_submit",
        )
    finally:
        conn.close()

    assert decision.status == "ALLOW"
    assert decision.evidence["current_asset_qty"] == 0.0
    assert decision.evidence["submit_qty"] == 0.0002
    assert decision.evidence["current_asset_qty_source"] == "broker_current_position"
    assert decision.evidence["submit_plan"]["qty"] == 0.0002


def test_pre_submit_requires_explicit_submit_qty(monkeypatch) -> None:
    def _daily_loss_state(*_args, **_kwargs) -> DailyLossEvaluation:
        return DailyLossEvaluation(
            blocked=False,
            reason="ok",
            reason_code="OK",
            decision="allow",
            evaluation_ts_ms=1_800_000_000_000,
            day_kst="2026-06-22",
            max_daily_loss_krw=50_000.0,
            start_equity=1_000_000.0,
            current_equity=1_000_000.0,
            loss_today=0.0,
            current_cash_krw=1_000_000.0,
            current_asset_qty=0.0,
            mark_price=100_000_000.0,
            mark_price_source="unit",
            details={},
        )

    monkeypatch.setattr("bithumb_bot.runtime_risk_engine.evaluate_daily_loss_state", _daily_loss_state)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._latest_position_entry_price", lambda _conn: None)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._count_orders_today", lambda _conn, _ts: 0)
    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.collect_risky_order_state",
        lambda *_args, **_kwargs: {},
    )

    conn = sqlite3.connect(":memory:")
    try:
        try:
            RuntimeRiskEngineAdapter(conn, policy=RiskPolicy(max_daily_loss_krw=50_000.0)).evaluate_pre_submit(
                plan=SubmitPlan(side="BUY", qty=0.0002, notional_krw=20_000.0, source="target_delta"),
                ts_ms=1_800_000_000_000,
                now_ms=1_800_000_000_000,
                cash=0.0,
                current_asset_qty=0.0,
                price=100_000_000.0,
                broker=object(),
                evaluation_origin="live_real_submit_authority_pre_submit",
            )
        except ValueError as exc:
            assert str(exc) == "pre_submit_submit_qty_required"
        else:  # pragma: no cover - assertion guard
            raise AssertionError("missing submit_qty must fail closed")
    finally:
        conn.close()


def test_pre_submit_sell_legacy_qty_does_not_overwrite_broker_current_asset_qty(monkeypatch) -> None:
    def _daily_loss_state(*_args, **_kwargs) -> DailyLossEvaluation:
        return DailyLossEvaluation(
            blocked=False,
            reason="ok",
            reason_code="OK",
            decision="allow",
            evaluation_ts_ms=1_800_000_000_000,
            day_kst="2026-06-22",
            max_daily_loss_krw=50_000.0,
            start_equity=1_000_000.0,
            current_equity=1_000_000.0,
            loss_today=0.0,
            current_cash_krw=1_000_000.0,
            current_asset_qty=0.0015,
            mark_price=100_000_000.0,
            mark_price_source="unit",
            details={"current_source": "broker_balance_snapshot"},
        )

    monkeypatch.setattr("bithumb_bot.runtime_risk_engine.evaluate_daily_loss_state", _daily_loss_state)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._latest_position_entry_price", lambda _conn: None)
    monkeypatch.setattr("bithumb_bot.runtime_risk_engine._count_orders_today", lambda _conn, _ts: 0)
    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.collect_risky_order_state",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine._record_typed_decision_identity",
        lambda *_args, **_kwargs: None,
    )

    conn = sqlite3.connect(":memory:")
    try:
        decision = RuntimeRiskEngineAdapter(conn, policy=RiskPolicy(max_daily_loss_krw=50_000.0)).evaluate_pre_submit(
            plan=SubmitPlan(side="SELL", qty=0.0004, notional_krw=40_000.0, source="target_delta"),
            ts_ms=1_800_000_000_000,
            now_ms=1_800_000_000_000,
            cash=0.0,
            submit_qty=0.0004,
            current_asset_qty=None,
            qty=9.0,
            price=100_000_000.0,
            broker=object(),
            evaluation_origin="live_real_submit_authority_pre_submit",
        )
    finally:
        conn.close()

    assert decision.evidence["current_asset_qty"] == 0.0015
    assert decision.evidence["submit_qty"] == 0.0004
    assert decision.evidence["current_asset_qty_source"] == "broker_current_position"
    assert decision.evidence["legacy_qty_ignored_as_current_asset_qty"] is True
