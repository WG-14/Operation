from __future__ import annotations

import sqlite3

import pytest

from bithumb_bot.config import settings
from bithumb_bot.execution_service import (
    ExecutionDecisionSummary,
    ExecutionSubmitPlan,
    LiveSignalExecutionService,
    TypedExecutionRequest,
)
from bithumb_bot.pre_submit_risk_coordinator import PreSubmitRiskCoordinator
from bithumb_bot.risk_contract import RiskDecision, RiskPolicy


def _payload() -> dict[str, object]:
    policy = RiskPolicy(source="unit", max_daily_loss_krw=50_000.0)
    plan = ExecutionSubmitPlan(
        side="BUY",
        source="target_delta",
        authority="canonical_target_delta_sizing",
        final_action="REBALANCE_TO_TARGET",
        qty=0.001,
        notional_krw=100_000.0,
        target_exposure_krw=100_000.0,
        current_effective_exposure_krw=0.0,
        delta_krw=100_000.0,
        submit_expected=True,
        pre_submit_proof_status="passed",
        block_reason="none",
        idempotency_key="unit-key",
        extra_payload={
            "portfolio_target_authoritative": True,
            "portfolio_target_hash": "sha256:portfolio",
            "allocation_decision_hash": "sha256:allocation",
            "strategy_contribution_hash": "sha256:strategy",
            "pre_submit_risk_required": True,
            "portfolio_risk_policy_hash": "sha256:" + "9" * 64,
            "strategy_risk_profiles": [
                {
                    "strategy_instance_id": "h74-source-observation",
                    "strategy_name": "daily_participation_sma",
                    "strategy_risk_profile_hash": "sha256:" + "8" * 64,
                    "risk_policy": policy.as_dict(),
                    "risk_policy_hash": policy.policy_hash(),
                }
            ],
        },
    )
    return plan.as_final_payload()


def _typed_plan_from_payload(payload: dict[str, object]) -> ExecutionSubmitPlan:
    extra = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "side",
            "source",
            "authority",
            "final_action",
            "qty",
            "notional_krw",
            "target_exposure_krw",
            "current_effective_exposure_krw",
            "delta_krw",
            "submit_expected",
            "pre_submit_proof_status",
            "block_reason",
            "idempotency_key",
        }
    }
    return ExecutionSubmitPlan(
        side=str(payload["side"]),
        source=str(payload["source"]),
        authority=str(payload["authority"]),
        final_action=str(payload["final_action"]),
        qty=float(payload["qty"]),
        notional_krw=float(payload["notional_krw"]),
        target_exposure_krw=float(payload["target_exposure_krw"]),
        current_effective_exposure_krw=float(payload["current_effective_exposure_krw"]),
        delta_krw=float(payload["delta_krw"]),
        submit_expected=bool(payload["submit_expected"]),
        pre_submit_proof_status=str(payload["pre_submit_proof_status"]),
        block_reason=str(payload["block_reason"]),
        idempotency_key=str(payload["idempotency_key"]),
        extra_payload=extra,
    )


def _summary(payload: dict[str, object]) -> ExecutionDecisionSummary:
    return ExecutionDecisionSummary(
        raw_signal=str(payload["side"]),
        final_signal=str(payload["side"]),
        final_action=str(payload["final_action"]),
        submit_expected=bool(payload["submit_expected"]),
        pre_submit_proof_status=str(payload["pre_submit_proof_status"]),
        block_reason=str(payload["block_reason"]),
        strategy_sell_candidate=None,
        residual_sell_candidate=None,
        target_exposure_krw=float(payload["target_exposure_krw"]),
        current_effective_exposure_krw=float(payload["current_effective_exposure_krw"]),
        tracked_residual_exposure_krw=None,
        buy_delta_krw=float(payload["delta_krw"]),
        residual_live_sell_mode="block",
        residual_buy_sizing_mode="block",
        residual_submit_plan=None,
        buy_submit_plan=None,
        target_shadow_decision=None,
        target_submit_plan=_typed_plan_from_payload(payload),
    )


def _decision(status: str, reason_code: str) -> RiskDecision:
    policy = RiskPolicy(source="unit", max_daily_loss_krw=50_000.0)
    return RiskDecision(
        evaluation_point="pre_submit",
        status=status,  # type: ignore[arg-type]
        reason_code=reason_code,
        reason=reason_code,
        allowed_actions=("BUY", "SELL", "HOLD") if status == "ALLOW" else ("HOLD",),
        recommended_action=None if status == "ALLOW" else "halt",
        risk_input_hash="sha256:" + "1" * 64,
        risk_policy_hash=policy.policy_hash(),
        risk_evidence_hash="sha256:" + "2" * 64,
        risk_decision_hash="sha256:" + "3" * 64,
        effective_limits=policy.effective_limits(),
        state_source="runtime_db_broker",
        evidence={
            "current_asset_qty": 0.0,
            "submit_qty": 0.001,
            "current_asset_qty_source": "broker_current_position",
            "submit_plan_qty_source": "submit_plan.qty",
        },
    )


def test_broker_submit_not_called_when_proof_persist_fails(monkeypatch) -> None:
    broker_submit_calls: list[object] = []
    conn = sqlite3.connect(":memory:")

    def _fake_evaluate(*_args, **_kwargs):
        return _decision("ALLOW", "OK")

    def _fail_persist(*_args, **_kwargs):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.RuntimeRiskEngineAdapter.evaluate_pre_submit",
        _fake_evaluate,
    )
    monkeypatch.setattr(
        "bithumb_bot.pre_submit_risk_coordinator.update_execution_plan_final_submit_payload",
        _fail_persist,
    )

    with pytest.raises(RuntimeError, match="persist failed"):
        PreSubmitRiskCoordinator().evaluate_and_persist(
            conn,
            payload=_payload(),
            broker=object(),
            ts_ms=1_800_000_000_000,
            market_price=100_000_000.0,
            field_name="target_submit_plan",
        )

    assert broker_submit_calls == []
    conn.close()


def test_failed_pre_submit_proof_persists_skipped_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}
    conn = sqlite3.connect(":memory:")

    def _fake_evaluate(*_args, **_kwargs):
        return _decision("BLOCK", "RISK_STATE_MISMATCH")

    def _capture_persist(_conn, *, final_submit_payload, persistence_status):
        captured["payload"] = dict(final_submit_payload)
        captured["persistence_status"] = persistence_status
        return {"updated": True}

    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.RuntimeRiskEngineAdapter.evaluate_pre_submit",
        _fake_evaluate,
    )
    monkeypatch.setattr(
        "bithumb_bot.pre_submit_risk_coordinator.update_execution_plan_final_submit_payload",
        _capture_persist,
    )

    result = PreSubmitRiskCoordinator().evaluate_and_persist(
        conn,
        payload=_payload(),
        broker=object(),
        ts_ms=1_800_000_000_000,
        market_price=100_000_000.0,
        field_name="target_submit_plan",
    )

    assert result.allowed is False
    assert captured["persistence_status"] == "post_proof_submit_skipped"
    payload = captured["payload"]
    assert payload["pre_submit_risk_reason_code"] == "RISK_STATE_MISMATCH"
    assert payload["final_submit_payload_persistence_status"] == "post_proof_submit_skipped"
    conn.close()


def test_pre_submit_uses_caller_connection(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    captured: dict[str, object] = {}
    original = {
        "MODE": settings.MODE,
        "LIVE_DRY_RUN": settings.LIVE_DRY_RUN,
        "LIVE_REAL_ORDER_ARMED": settings.LIVE_REAL_ORDER_ARMED,
        "EXECUTION_ENGINE": settings.EXECUTION_ENGINE,
    }

    def _fake_evaluate(self, **_kwargs):
        captured["adapter_conn"] = self.conn
        return _decision("ALLOW", "OK")

    def _capture_persist(persist_conn, *, final_submit_payload, persistence_status):
        captured["persist_conn"] = persist_conn
        return {"updated": True}

    monkeypatch.setattr(
        "bithumb_bot.runtime_risk_engine.RuntimeRiskEngineAdapter.evaluate_pre_submit",
        _fake_evaluate,
    )
    monkeypatch.setattr(
        "bithumb_bot.pre_submit_risk_coordinator.update_execution_plan_final_submit_payload",
        _capture_persist,
    )

    try:
        object.__setattr__(settings, "MODE", "live")
        object.__setattr__(settings, "LIVE_DRY_RUN", False)
        object.__setattr__(settings, "LIVE_REAL_ORDER_ARMED", True)
        object.__setattr__(settings, "EXECUTION_ENGINE", "target_delta")

        PreSubmitRiskCoordinator().evaluate_and_persist(
            conn,
            payload=_payload(),
            broker=object(),
            ts_ms=1_800_000_000_000,
            market_price=100_000_000.0,
            field_name="target_submit_plan",
        )

        assert captured["adapter_conn"] is conn
        assert captured["persist_conn"] is conn

        class RuntimeConnection:
            def __init__(self) -> None:
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                self.closed = True

        runtime_conn = RuntimeConnection()
        captured.clear()
        submit_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            "bithumb_bot.execution_service.ensure_db",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("live pre-submit path must use injected runtime db factory")
            ),
        )
        payload = _payload()
        service = LiveSignalExecutionService(
            broker=object(),
            executor=lambda _broker, signal, ts, market_price, **kwargs: submit_calls.append(
                {"signal": signal, "ts": ts, "market_price": market_price, "kwargs": dict(kwargs)}
            )
            or {"status": "submitted"},
            harmless_dust_recorder=lambda **_kwargs: False,
            db_factory=lambda: runtime_conn,
        )

        result = service.execute(
            TypedExecutionRequest(
                signal="BUY",
                ts=1_800_000_000_000,
                market_price=100_000_000.0,
                strategy_name="daily_participation_sma",
                execution_decision_summary=_summary(payload),
            )
        )

        assert result == {"status": "submitted"}
        assert captured["adapter_conn"] is runtime_conn
        assert captured["persist_conn"] is runtime_conn
        assert runtime_conn.committed is True
        assert runtime_conn.closed is True
        assert submit_calls
    finally:
        for key, value in original.items():
            object.__setattr__(settings, key, value)
        conn.close()
