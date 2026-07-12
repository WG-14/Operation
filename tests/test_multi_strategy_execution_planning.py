from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from operation.config import settings
from operation.db_core import ensure_db
from operation.run_loop_execution_planner import ExecutionPlanner
from operation.runtime.decision_persistence import DecisionPersistenceUnitOfWork
from operation.runtime_strategy_set import (
    RuntimeDecisionRequestBuilder,
    RuntimeMarketScope,
    RuntimeStrategyDecisionResultBundle,
    RuntimeStrategySet,
    RuntimeStrategySpec,
)
from operation.strategy_policy_contract import EntryExecutionIntent, PositionSnapshot, StrategyDecisionV2


@dataclass
class _Result:
    decision: StrategyDecisionV2
    base_context: dict[str, object]
    candle_ts: int
    market_price: float
    policy_hashes: dict[str, object]
    replay_fingerprint: dict[str, object]
    boundary: dict[str, object]

    def as_legacy_dict(self) -> dict[str, object]:
        return {
            **self.base_context,
            "strategy": self.decision.strategy_name,
            "signal": self.decision.final_signal,
            "reason": self.decision.final_reason,
            "ts": self.candle_ts,
            "last_close": self.market_price,
        }


class _Readiness:
    def as_dict(self) -> dict[str, object]:
        return {
            "broker_position_evidence": {"broker_qty_known": True, "broker_qty": 0.0},
            "projection_converged": True,
            "projection_convergence": {"converged": True},
            "broker_portfolio_converged": True,
            "open_order_count": 0,
            "unresolved_open_order_count": 0,
            "recovery_required_count": 0,
            "submit_unknown_count": 0,
            "accounting_projection_ok": True,
            "active_fee_accounting_blocker": False,
            "min_qty": 0.0001,
            "min_notional_krw": 5_000.0,
            "cash_available": 1_000_000.0,
        }


def _settings() -> object:
    return replace(
        settings,
        MODE="paper",
        PAIR="KRW-BTC",
        INTERVAL="1m",
        EXECUTION_ENGINE="target_delta",
        LIVE_DRY_RUN=False,
        LIVE_REAL_ORDER_ARMED=False,
    )


def _decision(signal: str) -> StrategyDecisionV2:
    return StrategyDecisionV2(
        strategy_name="safe_hold",
        raw_signal=signal,
        raw_reason=f"raw:{signal}",
        entry_signal=signal,
        entry_reason=f"entry:{signal}",
        exit_signal=signal,
        exit_reason=f"exit:{signal}",
        final_signal=signal,
        final_reason=f"final:{signal}",
        blocked_filters=(),
        entry_blocked=False,
        entry_block_reason=None,
        exit_rule=None,
        exit_evaluations=(),
        protective_exit_overrode_entry=False,
        exit_filter_suppression_prevented=False,
        position_snapshot=PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False),
        execution_intent=EntryExecutionIntent(
            side="BUY",
            intent="enter",
            pair="KRW-BTC",
            requires_execution_sizing=True,
            budget_fraction_of_cash=1.0,
            max_budget_krw=100_000.0,
        ),
        entry_decision=object(),  # type: ignore[arg-type]
        trace={"typed_result": signal},
        policy_hash="sha256:policy",
        policy_contract_hash="sha256:contract",
        policy_input_hash=f"sha256:input:{signal}",
        policy_decision_hash=f"sha256:decision:{signal}",
    )


def _result(signal: str, spec: RuntimeStrategySpec, *, candle_ts: int = 123) -> _Result:
    request = RuntimeDecisionRequestBuilder(settings_obj=_settings()).build_for_spec(
        spec,
        through_ts_ms=candle_ts,
    )
    fields = request.observability_fields()
    return _Result(
        decision=_decision(signal),
        base_context={
            **fields,
            "strategy": spec.strategy_name,
            "signal": signal,
            "reason": f"typed:{signal}",
            "market_price": 100_000_000.0,
        },
        candle_ts=candle_ts,
        market_price=100_000_000.0,
        policy_hashes={},
        replay_fingerprint={**fields, "candle_ts": candle_ts},
        boundary={"phase": "multi_strategy_contract_test"},
    )


def _bundle(*entries: tuple[str, RuntimeStrategySpec]) -> RuntimeStrategyDecisionResultBundle:
    specs = tuple(spec for _signal, spec in entries)
    return RuntimeStrategyDecisionResultBundle(
        strategy_set=RuntimeStrategySet(
            source="multi_strategy_contract_test",
            market_scope=RuntimeMarketScope(pair="KRW-BTC", interval="1m"),
            strategies=specs,
        ),
        results=tuple(_result(signal, spec) for signal, spec in entries),
    )


def _planner() -> ExecutionPlanner:
    return ExecutionPlanner(
        settings_obj=_settings(),
        readiness_snapshot_builder=lambda _conn: _Readiness(),
        target_state_resolver=lambda *_args, **_kwargs: {
            "previous_target_exposure_krw": 0.0,
            "target_policy_metadata": {},
        },
    )


def _spec(
    instance_id: str,
    *,
    priority: int = 10,
    weight: float = 1.0,
    desired_exposure_krw: float = 70_000.0,
    max_target_exposure_krw: float | None = None,
    pair: str = "KRW-BTC",
    interval: str = "1m",
) -> RuntimeStrategySpec:
    return RuntimeStrategySpec(
        "safe_hold",
        strategy_instance_id=instance_id,
        pair=pair,
        interval=interval,
        priority=priority,
        weight=weight,
        desired_exposure_krw=desired_exposure_krw,
        max_target_exposure_krw=max_target_exposure_krw,
    )


def test_buy_hold_uses_allocator_target_and_persists_single_pair_chain(tmp_path) -> None:
    buy = _spec("buy")
    hold = _spec("hold")
    bundle = _bundle(("BUY", buy), ("HOLD", hold))
    plan = _planner().plan_runtime_strategy_results(object(), bundle, updated_ts=456)

    assert plan.planning_error is None
    assert [item["signal_direction"] for item in plan.persistence_context["strategy_preferences"]] == [
        "BUY",
        "HOLD",
    ]
    assert plan.persistence_context["allocator_execution_signal"] == "BUY"
    assert plan.persistence_context["portfolio_target_authoritative"] is True
    assert plan.execution_plan_batch is not None
    assert len(plan.execution_plan_batch.pair_plans) == 1
    assert plan.execution_plan_batch.pair_plans[0].pair == "KRW-BTC"

    conn = ensure_db(str(tmp_path / "data" / "paper" / "trades" / "paper.sqlite"))
    try:
        persisted = DecisionPersistenceUnitOfWork().persist(
            conn,
            typed_bundle=bundle,
            planning_bundle=plan,
            context=dict(plan.persistence_context),
            strategy_name="multi_strategy",
            signal="BUY",
            reason="buy_weighted_target_from_allocator",
            updated_ts=456,
            settings_obj=_settings(),
        )
        assert persisted.decision_id is not None
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "runtime_strategy_set_manifest",
                "runtime_strategy_decision_bundle",
                "runtime_strategy_decision_result",
                "portfolio_allocation_decision",
                "execution_plan_batch",
                "execution_plan",
                "strategy_decisions",
                "orders",
                "fills",
            )
        }
    finally:
        conn.close()

    assert counts == {
        "runtime_strategy_set_manifest": 1,
        "runtime_strategy_decision_bundle": 1,
        "runtime_strategy_decision_result": 2,
        "portfolio_allocation_decision": 1,
        "execution_plan_batch": 1,
        "execution_plan": 1,
        "strategy_decisions": 1,
        "orders": 0,
        "fills": 0,
    }


def test_equal_priority_buy_sell_persists_conflict_without_submission(tmp_path) -> None:
    plan = _planner().plan_runtime_strategy_results(
        object(),
        _bundle(("BUY", _spec("buy")), ("SELL", _spec("sell"))),
        updated_ts=456,
    )

    assert plan.planning_error is None
    assert plan.persistence_context["allocation_primary_block_reason"] == "conflicting_equal_priority_signals"
    assert plan.submit_plan is None
    assert plan.execution_plan_batch is not None
    assert plan.execution_plan_batch.pair_plans[0].submit_expected is False
    conn = ensure_db(str(tmp_path / "data" / "paper" / "trades" / "conflict.sqlite"))
    try:
        persisted = DecisionPersistenceUnitOfWork().persist(
            conn,
            typed_bundle=_bundle(("BUY", _spec("buy")), ("SELL", _spec("sell"))),
            planning_bundle=plan,
            context=dict(plan.persistence_context),
            strategy_name="multi_strategy",
            signal="HOLD",
            reason="conflicting_equal_priority_signals",
            updated_ts=456,
            settings_obj=_settings(),
        )
        assert str(persisted.context["runtime_strategy_decision_bundle_hash"]).startswith("sha256:")
        assert str(persisted.context["portfolio_allocation_decision_hash"]).startswith("sha256:")
        assert str(persisted.context["execution_plan_batch_hash"]).startswith("sha256:")
        assert conn.execute("SELECT COUNT(*) FROM execution_plan").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    finally:
        conn.close()


def test_priority_and_weighted_exposure_are_deterministic_and_capped() -> None:
    high_sell = _spec("high_sell", priority=10)
    low_buy = _spec("low_buy", priority=20)
    first = _planner().plan_runtime_strategy_results(
        object(), _bundle(("BUY", low_buy), ("SELL", high_sell)), updated_ts=456
    )
    second = _planner().plan_runtime_strategy_results(
        object(), _bundle(("SELL", high_sell), ("BUY", low_buy)), updated_ts=456
    )
    assert first.persistence_context["allocation_decision_hash"] == second.persistence_context[
        "allocation_decision_hash"
    ]
    assert first.persistence_context["allocation_selected_strategy_instance_ids"] == ["high_sell"]

    weighted = _planner().plan_runtime_strategy_results(
        object(),
        _bundle(
            ("BUY", _spec("one", weight=1.0, desired_exposure_krw=100_000.0, max_target_exposure_krw=30_000.0)),
            ("BUY", _spec("two", weight=3.0, desired_exposure_krw=40_000.0, max_target_exposure_krw=20_000.0)),
        ),
        updated_ts=456,
    )
    target = weighted.persistence_context["portfolio_target"]
    assert target["target_exposure_krw"] == pytest.approx(50_000.0)
    assert target["exposure_cap_krw"] == pytest.approx(50_000.0)
    assert all(item["risk_budget_krw"] is None for item in weighted.persistence_context["allocation_contributions"])


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (_spec("other_pair", pair="KRW-ETH"), "multi_pair_runtime_unsupported"),
        (_spec("other_interval", interval="5m"), "single_interval_runtime_unsupported"),
    ],
)
def test_unsupported_runtime_scopes_fail_closed_before_execution_planning(
    spec: RuntimeStrategySpec,
    reason: str,
) -> None:
    plan = _planner().plan_runtime_strategy_results(
        object(), _bundle(("BUY", _spec("valid")), ("HOLD", spec)), updated_ts=456
    )
    assert plan.submit_plan is None
    assert reason in str(plan.planning_error)
    assert plan.persistence_context["execution_block_reason"] == "execution_decision_unavailable"


def test_result_without_declared_instance_fails_closed_without_name_fallback() -> None:
    declared = _spec("declared")
    missing = _spec("missing")
    with pytest.raises(ValueError, match="runtime_strategy_spec_missing:missing"):
        RuntimeStrategyDecisionResultBundle(
            strategy_set=RuntimeStrategySet(
                source="multi_strategy_contract_test",
                market_scope=RuntimeMarketScope(pair="KRW-BTC", interval="1m"),
                strategies=(declared,),
            ),
            results=(_result("BUY", missing),),
        )
