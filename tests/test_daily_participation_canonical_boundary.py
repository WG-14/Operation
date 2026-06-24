from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest

from bithumb_bot.core.sma_policy import PositionSnapshot
from bithumb_bot.research.experiment_manifest import ExecutionTimingPolicy
from bithumb_bot.strategy.daily_participation_policy import (
    DailyParticipationCountSnapshot,
    DailyParticipationPolicyConfig,
)
from bithumb_bot.strategy_evaluation_receipt import validate_strategy_evaluation_receipt
from bithumb_bot.strategy_plugins.daily_participation_sma import (
    DAILY_PARTICIPATION_SMA_PLUGIN,
    _daily_runtime_result_from_base,
    research_policy_decision_builder,
)
from bithumb_bot.strategy_policy_contract import StrategyDecisionV2
from tests.test_daily_participation_sma_backtest_integration import _dataset, _params


@dataclass(frozen=True)
class _BaseRuntimeResult:
    decision: StrategyDecisionV2
    base_context: dict[str, object]
    replay_fingerprint: dict[str, object]


def _research_decision() -> StrategyDecisionV2:
    dataset = _dataset()
    event = DAILY_PARTICIPATION_SMA_PLUGIN.research_event_builder(
        dataset=dataset,
        parameter_values=_params(),
        fee_rate=0.001,
        slippage_bps=0.0,
        execution_timing_policy=ExecutionTimingPolicy(),
    )[0]
    candle_index = next(index for index, candle in enumerate(dataset.candles) if candle.ts == event.candle_ts)
    return DAILY_PARTICIPATION_SMA_PLUGIN.research_policy_decision_builder(
        event=event,
        dataset=dataset,
        candle_index=candle_index,
        position=PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=True),
        parameter_values=_params(),
        fee_rate=0.001,
        slippage_bps=0.0,
        active_exit_policy={},
    )


def _base_decision() -> StrategyDecisionV2:
    position = PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False)
    return StrategyDecisionV2(
        strategy_name="sma_with_filter",
        raw_signal="HOLD",
        raw_reason="unit",
        entry_signal="HOLD",
        entry_reason="unit",
        exit_signal="HOLD",
        exit_reason="unit",
        final_signal="HOLD",
        final_reason="unit",
        blocked_filters=(),
        entry_blocked=False,
        entry_block_reason=None,
        exit_rule=None,
        exit_evaluations=(),
        protective_exit_overrode_entry=False,
        exit_filter_suppression_prevented=False,
        position_snapshot=position,
        execution_intent=None,
        entry_decision=None,
        trace={"blocked_filters": [], "final_exit_decision_input_hash": "sha256:exit-input"},
        policy_hash="sha256:base-policy",
        policy_contract_hash="sha256:base-contract",
        policy_input_hash="sha256:base-input",
        policy_decision_hash="sha256:base-decision",
    )


def _runtime_result():
    count = DailyParticipationCountSnapshot(
        count_basis="filled",
        timezone="Asia/Seoul",
        kst_day="2024-01-01",
        count_for_kst_day=0,
        timestamp_field="fill_ts",
        source="unit",
        rows=(),
        pair="KRW-BTC",
        strategy_instance_id="daily_participation_sma:runtime",
        event_set_hash="sha256:event-set",
        source_contract_hash="sha256:source-contract",
        query_contract_hash="sha256:query-contract",
    )
    return _daily_runtime_result_from_base(
        base_result=_BaseRuntimeResult(
            decision=_base_decision(),
            base_context={"pair": "KRW-BTC"},
            replay_fingerprint={"base": "unit"},
        ),
        participation_config=DailyParticipationPolicyConfig(
            enabled=True,
            timezone="Asia/Seoul",
            count_basis="filled",
            window_start_hour=0,
            window_end_hour=24,
            buy_fraction=0.05,
            max_order_krw=10000.0,
        ),
        count_snapshot=count,
        decision_ts=1_704_046_800_000,
    )


def test_research_daily_participation_final_decision_is_service_return_value() -> None:
    decision = _research_decision()
    receipt = decision.trace["strategy_evaluation_receipt"]

    assert decision.strategy_name == "daily_participation_sma"
    validate_strategy_evaluation_receipt(
        receipt=receipt,
        decision=decision,
        expected_input_bundle_hash=str(decision.trace["decision_input_bundle_hash"]),
        expected_strategy_name="daily_participation_sma",
        expected_mode="research_exploratory",
    )


def test_runtime_daily_participation_final_decision_is_service_return_value() -> None:
    result = _runtime_result()
    receipt = result.decision.trace["strategy_evaluation_receipt"]

    assert result.decision.strategy_name == "daily_participation_sma"
    assert result.base_context["strategy_evaluation_receipt"] == receipt
    validate_strategy_evaluation_receipt(
        receipt=receipt,
        decision=result.decision,
        expected_input_bundle_hash=str(result.decision.trace["decision_input_bundle_hash"]),
        expected_strategy_name="daily_participation_sma",
        expected_mode="runtime_replay",
    )


def test_manual_decision_boundary_label_without_service_receipt_is_rejected() -> None:
    decision = _base_decision()
    decision.trace["strategy_evaluation_provenance"] = {
        "decision_boundary": "StrategyDecisionService.evaluate"
    }

    with pytest.raises(ValueError, match="strategy_evaluation_receipt_missing"):
        validate_strategy_evaluation_receipt(receipt=decision.trace.get("strategy_evaluation_receipt"), decision=decision)


def test_daily_participation_receipt_required_for_promotion_mode() -> None:
    decision = _research_decision()

    assert "strategy_evaluation_receipt" in decision.trace
    assert decision.trace["strategy_evaluation_receipt"]["service_evaluation_hash"].startswith("sha256:")


def test_research_builder_does_not_directly_call_daily_participation_decision_helper() -> None:
    source = inspect.getsource(research_policy_decision_builder)

    assert "evaluate_daily_participation_sma_decision(" not in source
