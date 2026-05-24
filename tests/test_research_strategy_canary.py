from __future__ import annotations

import pytest

from bithumb_bot.approved_profile import ApprovedProfileError, runtime_contract_from_env_values
from bithumb_bot.research.dataset_snapshot import Candle, DatasetSnapshot
from bithumb_bot.research.decision_event import ResearchDecisionEvent
from bithumb_bot.research.experiment_manifest import DateRange
from bithumb_bot.research.strategy_registry import resolve_research_strategy
from bithumb_bot.research.strategy_spec import StrategySpecError, validate_parameter_space_against_strategy_spec


def _dataset() -> DatasetSnapshot:
    return DatasetSnapshot(
        snapshot_id="noop_canary_unit",
        source="unit",
        market="KRW-BTC",
        interval="1m",
        split_name="validation",
        date_range=DateRange("2026-01-01", "2026-01-02"),
        candles=tuple(
            Candle(index * 60_000, 100.0 + index, 100.0 + index, 100.0 + index, 100.0 + index, 1.0)
            for index in range(5)
        ),
    )


def test_research_decision_event_is_strategy_neutral() -> None:
    event = ResearchDecisionEvent(
        candle_ts=1,
        decision_ts=2,
        strategy_name="noop_baseline",
        strategy_version="noop_baseline.research_contract.v1",
        raw_signal="HOLD",
        final_signal="HOLD",
        reason="noop_baseline_hold",
        feature_snapshot={"close": 100.0},
        strategy_diagnostics={"hold_decision_count": 1},
    )

    assert event.strategy_name == "noop_baseline"
    assert event.feature_snapshot == {"close": 100.0}
    assert event.order_intent is None


def test_noop_baseline_runs_through_backtest_result_contract() -> None:
    runner = resolve_research_strategy("noop_baseline")

    result = runner(
        _dataset(),
        {"NOOP_DECISION_START_INDEX": 1},
        0.001,
        0.0,
        None,
        None,
        None,
        None,
        None,
    )

    assert result.candle_count == 5
    assert result.trades == ()
    assert result.metrics_v2 is not None
    assert result.execution_event_summary is not None
    assert result.resource_usage is not None
    assert result.resource_usage["common_decision_behavior_hash"].startswith("sha256:")
    assert result.resource_usage["strategy_behavior_hash"].startswith("sha256:")
    assert result.resource_usage["composite_behavior_hash"] == result.resource_usage["behavior_hash"]
    assert result.strategy_diagnostics is not None
    assert result.strategy_diagnostics["strategy_diagnostics_namespace"] == "noop_baseline"
    assert set(result.strategy_diagnostics["strategy_specific_diagnostics"]) == {"noop_baseline"}
    assert result.decisions
    first = result.decisions[0]
    assert first["strategy_name"] == "noop_baseline"
    assert first["strategy_plugin_contract"]["name"] == "noop_baseline"
    assert first["strategy_plugin_contract_hash"].startswith("sha256:")
    assert first["strategy_decision_contract_version"] == "research_noop_baseline_decision_contract.v1"
    assert first["execution_intent"] == "none"
    assert first["strategy_diagnostics_namespace"] == "noop_baseline"


def test_noop_baseline_parameter_validation_rejects_unknowns() -> None:
    with pytest.raises(StrategySpecError, match="unknown strategy parameter"):
        validate_parameter_space_against_strategy_spec(
            strategy_name="noop_baseline",
            parameter_space={"SMA_SHORT": (2,)},
            deployment_tier="research",
        )


def test_noop_baseline_runtime_env_contract_fails_closed() -> None:
    with pytest.raises(ApprovedProfileError, match="runtime_replay_unsupported_for_strategy:noop_baseline"):
        runtime_contract_from_env_values({"STRATEGY_NAME": "noop_baseline"})
