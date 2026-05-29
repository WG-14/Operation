from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bithumb_bot.canonical_decision import canonical_payload_hash
from bithumb_bot.research.backtest_pipeline import DefaultStrategyEvaluator
from bithumb_bot.research.backtest_stages import ReplayTick
from bithumb_bot.research.backtest_support import BacktestRunContext
from bithumb_bot.research.dataset_snapshot import Candle, DatasetSnapshot
from bithumb_bot.research.decision_event import ResearchDecisionEvent
from bithumb_bot.research.experiment_manifest import DateRange
from bithumb_bot.research.strategy_registry import list_research_strategy_plugins
from bithumb_bot.research.strategy_registry import ResearchStrategyPlugin, StrategyRuntimeCapabilities
import bithumb_bot.research.strategy_registry as strategy_registry
from bithumb_bot.research.strategy_spec import StrategySpec
from bithumb_bot.strategy_policy_contract import PositionSnapshot, StrategyDecisionV2


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_backtest_kernel_stays_strategy_neutral() -> None:
    source = _source("src/bithumb_bot/research/backtest_kernel.py")

    forbidden = (
        "sma_with_filter",
        "SMA_",
        "SmaPolicyConfig",
        "curr_s",
        "prev_s",
        "opposite_cross",
    )
    assert all(token not in source for token in forbidden)
    assert "ResearchDecisionEvent" in source
    assert "DefaultBacktestPipeline" in source


def test_backtest_kernel_is_orchestration_facade_not_transaction_script() -> None:
    source = _source("src/bithumb_bot/research/backtest_kernel.py")

    forbidden = (
        "apply_pending_fills(",
        "research_policy_decision_builder(",
        "merge_exit_rules(",
        "build_typed_execution_decision_summary(",
        "SignalExecutionRequest(",
        "pending_trade_from_fill(",
        "record_equity_mark(",
        "build_metrics_v2(",
    )

    assert all(token not in source for token in forbidden)
    assert "BacktestKernel().run(" in source


def test_default_backtest_authority_calls_live_inside_stage_classes() -> None:
    pipeline_source = _source("src/bithumb_bot/research/backtest_pipeline.py")
    runner_source = _source("src/bithumb_bot/research/backtest_stage_runner.py")
    loop_source = _source("src/bithumb_bot/research/backtest_loop.py")

    assert "plugin.research_policy_decision_builder" in pipeline_source
    assert "builder(**policy_builder_kwargs)" in pipeline_source
    assert "class DefaultStrategyEvaluator" in pipeline_source
    assert "merge_exit_rules(" in pipeline_source
    assert "class DefaultRiskGate" in pipeline_source
    assert "from .execution_simulator_stage import DefaultExecutionSimulator" in pipeline_source
    assert "SignalExecutionRequest(" not in pipeline_source
    assert "class DefaultExecutionSimulator" not in pipeline_source
    assert "class DefaultExecutionSimulator" in _source("src/bithumb_bot/research/execution_simulator_stage.py")

    for forbidden in (
        "research_policy_decision_builder(",
        "merge_exit_rules(",
        "SignalExecutionRequest(",
        "ResearchVirtualExecutionService(",
        "support.apply_pending_fills(",
    ):
        assert forbidden not in runner_source

    assert "DefaultBacktestPipeline().run(" in loop_source
    assert "research_policy_decision_builder(" not in loop_source
    assert "support.apply_pending_fills(" not in loop_source
    assert "SignalExecutionRequest(" not in loop_source


def test_backtest_stage_runner_observability_is_extracted_to_named_components() -> None:
    runner_source = _source("src/bithumb_bot/research/backtest_stage_runner.py")

    assert "DecisionPayloadBuilder()" in runner_source
    assert "AuditTraceRecorder()" in runner_source
    assert "StageTraceRecorder()" in runner_source
    assert "BacktestResultAssembler()" in runner_source
    assert "support.research_decision_payload(" not in runner_source
    assert "build_metrics_v2(" not in runner_source
    assert "aggregate_regime_coverage(" not in runner_source
    assert "aggregate_regime_performance(" not in runner_source
    assert "complete_audit_trace(" not in runner_source
    assert "support.trace_decision(" not in runner_source
    assert "support.trace_execution(" not in runner_source
    assert "support.trace_equity_mark(" not in runner_source
    assert "experiment_recorder.record_stage(" not in runner_source

    assert "class AuditTraceRecorder" in _source("src/bithumb_bot/research/audit_trace_recorder.py")
    assert "class DecisionPayloadBuilder" in _source("src/bithumb_bot/research/decision_payload.py")
    assert "class StageTraceRecorder" in _source("src/bithumb_bot/research/stage_trace_recorder.py")
    assert "class BacktestResultAssembler" in _source(
        "src/bithumb_bot/research/backtest_result_assembler.py"
    )


def test_execution_planning_helpers_are_not_canonical_in_backtest_loop() -> None:
    loop_source = _source("src/bithumb_bot/research/backtest_loop.py")
    planning_source = _source("src/bithumb_bot/research/execution_planning.py")

    assert "class ResearchExecutionPlanBundle" not in loop_source
    assert "def _research_execution_plan_bundle(" not in loop_source
    assert "def _execution_plan_evidence(" not in loop_source
    assert "build_typed_execution_decision_summary(" not in loop_source
    assert "from .execution_planning import" in loop_source

    assert "class ResearchExecutionPlanBundle" in planning_source
    assert "def _research_execution_plan_bundle(" in planning_source
    assert "def _execution_plan_evidence(" in planning_source


def test_production_strategy_decisions_go_through_canonical_service() -> None:
    allowed_files = {
        "src/bithumb_bot/strategy_decision_service.py",
        "src/bithumb_bot/runtime_strategy_decision.py",
        "src/bithumb_bot/strategy/sma_policy_strategy.py",
    }
    violations: list[str] = []
    for path in (ROOT / "src/bithumb_bot").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "decide_snapshot":
                    violations.append(f"{rel}:{node.lineno}")

    assert violations == []


def test_all_promotion_grade_plugins_fail_closed_without_typed_decision() -> None:
    plugins = [
        plugin
        for plugin in list_research_strategy_plugins()
        if plugin.runtime_capabilities.promotion_runtime_decisions_supported
    ]

    assert {plugin.name for plugin in plugins} >= {"sma_with_filter", "canary_non_sma", "safe_hold"}
    for plugin in plugins:
        assert plugin.runtime_decision_adapter_factory is not None
        assert plugin.policy_assembly_factory is not None
        if plugin.research_runnable:
            assert plugin.research_policy_decision_builder is not None

    evaluator_source = _source("src/bithumb_bot/research/backtest_pipeline.py")
    assert "research_strategy_decision_promotion_fields_missing" in evaluator_source
    assert "if promotion_grade_policy_required and policy_decision is None" in evaluator_source
    assert "StrategyDecisionService.evaluate" in evaluator_source


def test_promotion_grade_strategy_decision_fails_without_service_provenance(monkeypatch) -> None:
    strategy_name = "unit_no_service_provenance"
    spec = StrategySpec(
        strategy_name=strategy_name,
        strategy_version="unit_no_service_provenance.research_contract.v1",
        accepted_parameter_names=(),
        required_parameter_names=(),
        behavior_affecting_parameter_names=(),
        metadata_only_parameter_names=(),
        research_only_parameter_names=(),
        default_parameters={},
        decision_contract_version="unit_no_service_provenance_decision.v1",
        required_data=("candles",),
        optional_data=(),
        exit_policy_schema={"schema_version": 1, "rules": ()},
    )

    def _policy_builder(
        *,
        event,
        dataset,
        candle_index,
        position,
        parameter_values,
        fee_rate,
        slippage_bps,
        active_exit_policy,
        buy_fraction=0.0,
        **_extra,
    ):
        del dataset, candle_index, parameter_values, fee_rate, slippage_bps, active_exit_policy, buy_fraction
        policy_input = {"candle_ts": int(event.candle_ts), "position": position.terminal_state}
        policy_decision = {"final_signal": "HOLD", "final_reason": "unit_hold"}
        return StrategyDecisionV2(
            strategy_name=strategy_name,
            raw_signal="HOLD",
            raw_reason="unit_hold",
            entry_signal="HOLD",
            entry_reason="unit_hold",
            exit_signal="HOLD",
            exit_reason="unit_hold",
            final_signal="HOLD",
            final_reason="unit_hold",
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
            trace={"schema_version": 1},
            policy_hash=canonical_payload_hash(
                {"policy_input": policy_input, "policy_decision": policy_decision}
            ),
            policy_contract_hash=canonical_payload_hash({"strategy_name": strategy_name}),
            policy_input_hash=canonical_payload_hash(policy_input),
            policy_decision_hash=canonical_payload_hash(policy_decision),
        )

    plugin = ResearchStrategyPlugin(
        name=strategy_name,
        version=spec.strategy_version,
        spec=spec,
        required_data=spec.required_data,
        optional_data=spec.optional_data,
        runner=lambda **_: None,
        research_event_builder=lambda **_: (),
        runtime_replay_builder=None,
        runtime_parameter_adapter=lambda _env: {},
        decision_contract_version=spec.decision_contract_version,
        diagnostics_namespace=strategy_name,
        research_policy_decision_builder=_policy_builder,
        runtime_decision_adapter_factory=lambda: object(),
        policy_assembly_factory=lambda: object(),
        runtime_capabilities=StrategyRuntimeCapabilities(
            promotion_runtime_decisions_supported=True,
            runtime_replay_supported=False,
            research_only=False,
            baseline_only=False,
            live_dry_run_allowed=False,
            live_real_order_allowed=False,
            approved_profile_required=True,
        ),
    )
    monkeypatch.setitem(strategy_registry._RESEARCH_STRATEGY_PLUGINS, strategy_name, plugin)
    dataset = DatasetSnapshot(
        snapshot_id="unit_no_service_provenance",
        source="unit",
        market="KRW-BTC",
        interval="1m",
        split_name="validation",
        date_range=DateRange("2026-01-01", "2026-01-02"),
        candles=(Candle(0, 100.0, 100.0, 100.0, 100.0, 1.0),),
    )
    event = ResearchDecisionEvent(
        candle_ts=0,
        decision_ts=60_000,
        strategy_name=strategy_name,
        strategy_version=spec.strategy_version,
        raw_signal="HOLD",
        final_signal="HOLD",
        reason="unit_hold",
        feature_snapshot={},
        strategy_diagnostics={},
    )

    with pytest.raises(ValueError, match="strategy_evaluation_provenance"):
        DefaultStrategyEvaluator().evaluate(
            ReplayTick(candle=dataset.candles[0], candle_index=0, candle_ts=0, decision_ts=60_000, event=event),
            PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False),
            {
                "dataset": dataset,
                "strategy_name": strategy_name,
                "parameter_values": {},
                "fee_rate": 0.0,
                "slippage_bps": 0.0,
                "active_exit_policy": {},
                "buy_fraction": 1.0,
                "run_context": BacktestRunContext(policy_materialization_mode="research_promotion"),
            },
        )


def test_all_promotion_grade_plugins_fail_closed_without_typed_submit_plan() -> None:
    plugins = [
        plugin
        for plugin in list_research_strategy_plugins()
        if plugin.runtime_capabilities.promotion_runtime_decisions_supported
    ]

    assert plugins
    stage_source = _source("src/bithumb_bot/research/execution_simulator_stage.py")
    service_source = _source("src/bithumb_bot/research/execution_simulator.py")
    assert "raise ValueError(\"research_submit_plan_missing\")" in stage_source
    assert "research_dict_only_submit_plan_not_authority" in service_source


def test_runtime_production_modules_do_not_import_legacy_db_strategies() -> None:
    forbidden = {
        "LegacyDbStrategy",
        "create_legacy_db_strategy",
        "SmaCrossStrategy",
        "LegacySmaWithFilterDbAdapter",
    }
    allowed = {
        "src/bithumb_bot/compat/strategy.py",
        "src/bithumb_bot/compat/strategy_registry.py",
        "src/bithumb_bot/compat/sma_legacy_adapter.py",
        "src/bithumb_bot/run_loop_compatibility.py",
        "src/bithumb_bot/strategy/sma_legacy_adapter.py",
    }
    violations: list[str] = []
    for path in (ROOT / "src/bithumb_bot").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        source = path.read_text(encoding="utf-8-sig")
        for token in forbidden:
            if token in source:
                violations.append(f"{rel}:{token}")

    assert violations == []


def test_backtest_engine_is_compatibility_only_for_sma_event_generation() -> None:
    source = _source("src/bithumb_bot/research/backtest_engine.py")

    forbidden = (
        "SmaWithFilterDecisionAdapter",
        "_rolling_sma_values",
        "_rolling_close_range_ratios",
        "_overextended_return_ratios",
        "class Sma",
        "curr_s",
        "prev_s",
    )
    assert all(token not in source for token in forbidden)
    assert "Compatibility wrapper" in source


def test_backtest_runner_is_strategy_neutral() -> None:
    source = _source("src/bithumb_bot/research/backtest_runner.py")

    forbidden = (
        "sma_with_filter",
        "SMA_",
        "legacy_disabled_filter_defaults",
        "SmaWithFilter",
        "noop_baseline",
        "buy_and_hold_baseline",
    )
    assert all(token not in source for token in forbidden)
    assert "research_event_builder" in source
    assert "research_parameter_materializer" in source


def test_backtest_support_does_not_import_backtest_engine() -> None:
    source = _source("src/bithumb_bot/research/backtest_support.py")

    assert "backtest_engine" not in source


def test_strategy_registry_does_not_import_engine_owned_runners() -> None:
    source = _source("src/bithumb_bot/research/strategy_registry.py")

    forbidden = (
        "from .backtest_engine import",
        "run_sma_backtest",
        "run_noop_baseline_backtest",
        "run_buy_and_hold_baseline_backtest",
        "_rolling_sma_values",
        "_rolling_close_range_ratios",
        "_overextended_return_ratios",
        "build_sma_with_filter_research_events",
        "build_noop_baseline_events",
        "build_buy_and_hold_baseline_events",
        "_SMA_WITH_FILTER_PLUGIN",
        "_NOOP_BASELINE_PLUGIN",
        "_BUY_AND_HOLD_BASELINE_PLUGIN",
    )
    assert all(token not in source for token in forbidden)
    assert "ResearchStrategyPlugin(" not in source


def test_active_research_modules_do_not_import_common_types_from_backtest_engine() -> None:
    active_modules = (
        "src/bithumb_bot/research/validation_protocol.py",
    )
    for module in active_modules:
        source = _source(module)
        assert "from .backtest_engine import" not in source
        assert "backtest_engine import" not in source


def test_research_runnable_plugins_declare_event_builders_and_capabilities() -> None:
    for plugin in list_research_strategy_plugins():
        assert plugin.runtime_capabilities is not None
        payload = plugin.contract_payload()
        assert "research_event_builder_supported" in payload
        if payload["research_runnable"]:
            assert payload["research_event_builder_supported"] is True
            assert payload["research_event_builder_module"]


def test_non_sma_canary_uses_plugin_event_builder_contract() -> None:
    plugins = {plugin.name: plugin for plugin in list_research_strategy_plugins()}
    plugin = plugins["canary_non_sma"]
    payload = plugin.contract_payload()

    assert payload["research_event_builder_supported"] is True
    assert payload["research_event_builder_module"] == "bithumb_bot.strategy_plugins.canary_non_sma"
    assert payload["runner_module"] == "bithumb_bot.strategy_plugins.canary_non_sma"
