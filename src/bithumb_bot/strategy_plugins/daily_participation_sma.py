from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from bithumb_bot.core.sma_policy import (
    EntryExecutionIntent,
    ExecutionConstraintSnapshot,
    MarketWindow,
    PositionSnapshot,
    SmaPolicyConfig,
    _stable_hash,
)
from bithumb_bot.research.backtest_types import BacktestRun, BacktestRunContext
from bithumb_bot.research.dataset_snapshot import DatasetSnapshot
from bithumb_bot.research.execution_model import ExecutionModel
from bithumb_bot.research.experiment_manifest import ExecutionTimingPolicy, PortfolioPolicy
from bithumb_bot.research.strategy_registry import ResearchStrategyPlugin
from bithumb_bot.research.strategy_registry import RuntimeParameterAdapter
from bithumb_bot.research.strategy_spec import (
    SMA_WITH_FILTER_SPEC,
    StrategyParameterSchema,
    StrategySpec,
    materialized_strategy_parameters_hash,
    materialize_strategy_parameters,
)
from bithumb_bot.strategy.daily_participation_policy import (
    DailyParticipationPolicyConfig,
    DailyParticipationStateSnapshot,
    build_research_daily_count_snapshot,
    evaluate_daily_participation_policy,
    require_runtime_comparable_daily_count_snapshot,
)
from bithumb_bot.runtime.daily_participation_count_provider import build_runtime_daily_count_snapshot_from_sqlite
from bithumb_bot.strategy.exit_rules import ExitPolicyConfig
from bithumb_bot.strategy.sma_decision_assembler import evaluate_sma_final_decision
from bithumb_bot.strategy_decision_input import StrategyDecisionInputBundle
from bithumb_bot.strategy_decision_service import StrategyDecisionService, StrategyEvaluationRequest
from bithumb_bot.strategy_policy_contract import StrategyDecisionV2
from bithumb_bot.strategy_authoring import (
    PromotionGradeStrategyExtension,
    build_live_eligible_strategy_plugin,
    research_plugin_from_event_builder,
)
from bithumb_bot.strategy_plugins.daily_participation_contract import DAILY_PARTICIPATION_DECISION_EVIDENCE_CONTRACT
from bithumb_bot.strategy_plugins.daily_participation_diagnostics import daily_participation_diagnostics_count_builder
from bithumb_bot.strategy_plugins.sma_with_filter_contract import sma_runtime_data_requirements
from bithumb_bot.strategy_plugins.sma_with_filter_assembly import MaterializationMode
from bithumb_bot.strategy_plugins.sma_with_filter_assembly import SmaWithFilterPolicyAssembly
from bithumb_bot.strategy_plugins.sma_with_filter_projector import SmaWithFilterSnapshotProjector
from bithumb_bot.strategy_plugins.sma_with_filter_events import SmaWithFilterDecisionAdapter
from bithumb_bot.runtime_adapters.daily_participation_sma import DailyParticipationSmaRuntimeDecisionAdapter
from bithumb_bot.runtime_adapters.sma_with_filter import build_sma_with_filter_runtime_feature_snapshot


DAILY_PARTICIPATION_PARAMETERS: tuple[str, ...] = (
    "DAILY_PARTICIPATION_ENABLED",
    "DAILY_PARTICIPATION_TIMEZONE",
    "DAILY_PARTICIPATION_COUNT_BASIS",
    "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST",
    "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST",
    "DAILY_PARTICIPATION_BUY_FRACTION",
    "DAILY_PARTICIPATION_MAX_ORDER_KRW",
    "DAILY_PARTICIPATION_FALLBACK_MODE",
)


DAILY_PARTICIPATION_SMA_SPEC = StrategySpec(
    strategy_name="daily_participation_sma",
    strategy_version="daily_participation_sma.research_runtime_contract.v1",
    accepted_parameter_names=tuple(SMA_WITH_FILTER_SPEC.accepted_parameter_names) + DAILY_PARTICIPATION_PARAMETERS,
    required_parameter_names=tuple(SMA_WITH_FILTER_SPEC.required_parameter_names),
    behavior_affecting_parameter_names=tuple(SMA_WITH_FILTER_SPEC.behavior_affecting_parameter_names)
    + DAILY_PARTICIPATION_PARAMETERS,
    metadata_only_parameter_names=(),
    research_only_parameter_names=SMA_WITH_FILTER_SPEC.research_only_parameter_names,
    default_parameters={
        **SMA_WITH_FILTER_SPEC.default_parameters,
        "DAILY_PARTICIPATION_ENABLED": False,
        "DAILY_PARTICIPATION_TIMEZONE": "Asia/Seoul",
        "DAILY_PARTICIPATION_COUNT_BASIS": "filled",
        "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0,
        "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
        "DAILY_PARTICIPATION_BUY_FRACTION": 0.05,
        "DAILY_PARTICIPATION_MAX_ORDER_KRW": 10000.0,
        "DAILY_PARTICIPATION_FALLBACK_MODE": "unconditional_participation",
    },
    parameter_schema=SMA_WITH_FILTER_SPEC.parameter_schema
    + (
        StrategyParameterSchema("DAILY_PARTICIPATION_ENABLED", "bool", unit="enabled_flag"),
        StrategyParameterSchema("DAILY_PARTICIPATION_TIMEZONE", "str", enum=("Asia/Seoul", "KST"), unit="timezone"),
        StrategyParameterSchema(
            "DAILY_PARTICIPATION_COUNT_BASIS",
            "str",
            enum=("intent", "submit_expected", "submitted", "filled", "closed_trade"),
            unit="count_basis",
        ),
        StrategyParameterSchema("DAILY_PARTICIPATION_WINDOW_START_HOUR_KST", "int", min_value=0, max_value=23, unit="hour"),
        StrategyParameterSchema("DAILY_PARTICIPATION_WINDOW_END_HOUR_KST", "int", min_value=1, max_value=24, unit="hour"),
        StrategyParameterSchema("DAILY_PARTICIPATION_BUY_FRACTION", "float", min_value=0.0, max_value=1.0, unit="cash_fraction"),
        StrategyParameterSchema("DAILY_PARTICIPATION_MAX_ORDER_KRW", "float", min_value=0.0, unit="krw"),
        StrategyParameterSchema(
            "DAILY_PARTICIPATION_FALLBACK_MODE",
            "str",
            enum=("unconditional_participation", "requires_base_safety_filter", "disabled"),
            unit="fallback_contract",
        ),
    ),
    decision_contract_version="daily_participation_sma_decision_contract.v1",
    required_data=SMA_WITH_FILTER_SPEC.required_data,
    optional_data=SMA_WITH_FILTER_SPEC.optional_data,
    exit_policy_schema=SMA_WITH_FILTER_SPEC.exit_policy_schema,
)


def daily_participation_config_from_parameters(values: dict[str, Any]) -> DailyParticipationPolicyConfig:
    return DailyParticipationPolicyConfig(
        enabled=bool(values["DAILY_PARTICIPATION_ENABLED"]),
        timezone=str(values["DAILY_PARTICIPATION_TIMEZONE"]),
        count_basis=str(values["DAILY_PARTICIPATION_COUNT_BASIS"]),  # type: ignore[arg-type]
        window_start_hour=int(values["DAILY_PARTICIPATION_WINDOW_START_HOUR_KST"]),
        window_end_hour=int(values["DAILY_PARTICIPATION_WINDOW_END_HOUR_KST"]),
        buy_fraction=float(values["DAILY_PARTICIPATION_BUY_FRACTION"]),
        max_order_krw=float(values["DAILY_PARTICIPATION_MAX_ORDER_KRW"]),
        fallback_mode=str(values.get("DAILY_PARTICIPATION_FALLBACK_MODE", "unconditional_participation")),  # type: ignore[arg-type]
    )


def materialize_daily_participation_sma_parameters(
    *,
    plugin: ResearchStrategyPlugin,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    context: BacktestRunContext | None = None,
) -> dict[str, Any]:
    del plugin, context
    values = materialize_strategy_parameters(
        "daily_participation_sma",
        parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )
    values.setdefault("DAILY_PARTICIPATION_FALLBACK_MODE", "unconditional_participation")
    daily_participation_config_from_parameters(values)
    return values


def runtime_parameters_from_env(env: dict[str, str]) -> dict[str, Any]:
    from bithumb_bot.research import sma_with_filter_plugin as base_runtime

    values = base_runtime.runtime_parameters_from_env(env)

    def _value(key: str, default: str) -> str:
        return str(env.get(key, default)).strip() or default

    values.update(
        {
            "DAILY_PARTICIPATION_ENABLED": _value("DAILY_PARTICIPATION_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            "DAILY_PARTICIPATION_TIMEZONE": _value("DAILY_PARTICIPATION_TIMEZONE", "Asia/Seoul"),
            "DAILY_PARTICIPATION_COUNT_BASIS": _value("DAILY_PARTICIPATION_COUNT_BASIS", "filled"),
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": int(
                _value("DAILY_PARTICIPATION_WINDOW_START_HOUR_KST", "0")
            ),
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": int(
                _value("DAILY_PARTICIPATION_WINDOW_END_HOUR_KST", "24")
            ),
            "DAILY_PARTICIPATION_BUY_FRACTION": float(_value("DAILY_PARTICIPATION_BUY_FRACTION", "0.05")),
            "DAILY_PARTICIPATION_MAX_ORDER_KRW": float(_value("DAILY_PARTICIPATION_MAX_ORDER_KRW", "10000")),
            "DAILY_PARTICIPATION_FALLBACK_MODE": _value(
                "DAILY_PARTICIPATION_FALLBACK_MODE",
                "unconditional_participation",
            ),
        }
    )
    return values


def runtime_parameters_from_settings(cfg: object) -> dict[str, Any]:
    from bithumb_bot.research import sma_with_filter_plugin as base_runtime

    values = base_runtime.runtime_parameters_from_settings(cfg)
    values.update(
        {
            "DAILY_PARTICIPATION_ENABLED": bool(getattr(cfg, "DAILY_PARTICIPATION_ENABLED", False)),
            "DAILY_PARTICIPATION_TIMEZONE": str(getattr(cfg, "DAILY_PARTICIPATION_TIMEZONE", "Asia/Seoul")),
            "DAILY_PARTICIPATION_COUNT_BASIS": str(getattr(cfg, "DAILY_PARTICIPATION_COUNT_BASIS", "filled")),
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": int(
                getattr(cfg, "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST", 0)
            ),
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": int(
                getattr(cfg, "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST", 24)
            ),
            "DAILY_PARTICIPATION_BUY_FRACTION": float(getattr(cfg, "DAILY_PARTICIPATION_BUY_FRACTION", 0.05)),
            "DAILY_PARTICIPATION_MAX_ORDER_KRW": float(getattr(cfg, "DAILY_PARTICIPATION_MAX_ORDER_KRW", 10000.0)),
            "DAILY_PARTICIPATION_FALLBACK_MODE": str(
                getattr(cfg, "DAILY_PARTICIPATION_FALLBACK_MODE", "unconditional_participation")
            ),
        }
    )
    return values


class DailyParticipationSmaPolicyAssembly:
    contract_version = "daily_participation_sma_policy_assembly.v1"

    def __init__(self) -> None:
        self.base = SmaWithFilterPolicyAssembly()

    def materialize_parameters(
        self,
        parameters: dict[str, Any],
        materialization_mode: MaterializationMode = MaterializationMode.RUNTIME_REPLAY,
    ) -> dict[str, Any]:
        values = materialize_strategy_parameters(
            "daily_participation_sma",
            dict(parameters),
            fee_rate=0.0,
            slippage_bps=0.0,
        )
        daily_participation_config_from_parameters(values)
        return values


def policy_assembly_factory() -> DailyParticipationSmaPolicyAssembly:
    return DailyParticipationSmaPolicyAssembly()


def runtime_decision_adapter_factory() -> DailyParticipationSmaRuntimeDecisionAdapter:
    return DailyParticipationSmaRuntimeDecisionAdapter()


@dataclass(frozen=True)
class DailyParticipationSmaDecisionConfig:
    base_config: SmaPolicyConfig | None
    participation_config: DailyParticipationPolicyConfig
    participation_state: DailyParticipationStateSnapshot
    count_snapshot: Any
    base_decision: StrategyDecisionV2 | None = None
    signal_context_extra: dict[str, object] | None = None

    def policy_input_payload(self) -> dict[str, object]:
        base_payload = {}
        if self.base_config is not None:
            base_payload = (
                self.base_config.policy_input_payload()
                if hasattr(self.base_config, "policy_input_payload")
                else dict(getattr(self.base_config, "__dict__", {}))
            )
        return {
            "schema_version": 1,
            "base_config": base_payload,
            "participation_config": self.participation_config.policy_payload(),
            "participation_state": self.participation_state.snapshot_payload(
                config=self.participation_config
            ),
            "daily_count_snapshot": (
                self.count_snapshot.as_dict()
                if hasattr(self.count_snapshot, "as_dict")
                else {}
            ),
            "base_decision_hash": (
                self.base_decision.policy_decision_hash
                if self.base_decision is not None
                else ""
            ),
        }


@dataclass(frozen=True)
class DailyParticipationSmaStrategy:
    name: str = "daily_participation_sma"

    def decide_snapshot(
        self,
        *,
        market: object,
        position: PositionSnapshot,
        config: object,
        execution_context: ExecutionConstraintSnapshot,
        exit_policy_config: object | None = None,
        rule_sources: dict[str, str] | None = None,
    ) -> StrategyDecisionV2:
        if not isinstance(config, DailyParticipationSmaDecisionConfig):
            raise TypeError("daily_participation_sma_decision_config_invalid")
        base = config.base_decision
        if base is None:
            base = evaluate_sma_final_decision(
                market=market,  # type: ignore[arg-type]
                position=position,
                config=config.base_config,  # type: ignore[arg-type]
                execution_context=execution_context,
                exit_policy_config=exit_policy_config,  # type: ignore[arg-type]
                signal_context_extra=config.signal_context_extra,
                rule_sources=rule_sources,
            )
        return _compose_daily_participation_decision(
            base=base,
            market=market,
            participation_config=config.participation_config,
            participation_state=config.participation_state,
            count_snapshot=config.count_snapshot,
        )


@dataclass(frozen=True)
class _RuntimeDailyMarket:
    pair: str

    def policy_input_payload(self) -> dict[str, object]:
        return {"schema_version": 1, "pair": self.pair, "source": "runtime_base_sma_result"}


def evaluate_daily_participation_sma_decision(
    *,
    market: MarketWindow,
    position: PositionSnapshot,
    config: SmaPolicyConfig,
    execution_context: ExecutionConstraintSnapshot,
    exit_policy_config: ExitPolicyConfig,
    participation_config: DailyParticipationPolicyConfig,
    participation_state: DailyParticipationStateSnapshot,
    count_snapshot: Any,
    signal_context_extra: dict[str, object] | None = None,
    rule_sources: dict[str, str] | None = None,
):
    strategy = DailyParticipationSmaStrategy()
    composite_config = DailyParticipationSmaDecisionConfig(
        base_config=config,
        participation_config=participation_config,
        participation_state=participation_state,
        count_snapshot=count_snapshot,
        signal_context_extra=signal_context_extra,
    )
    return strategy.decide_snapshot(
        market=market,
        position=position,
        config=composite_config,
        execution_context=execution_context,
        exit_policy_config=exit_policy_config,
        rule_sources=rule_sources,
    )


def _compose_daily_participation_decision(
    *,
    base: StrategyDecisionV2,
    market: object,
    participation_config: DailyParticipationPolicyConfig,
    participation_state: DailyParticipationStateSnapshot,
    count_snapshot: Any,
) -> StrategyDecisionV2:
    base_entry_signal = "BUY" if base.final_signal == "BUY" else "HOLD"
    participation = evaluate_daily_participation_policy(config=participation_config, state=participation_state)
    final_signal = base.final_signal
    final_reason = base.final_reason
    entry_signal_source = "sma_cross" if base.final_signal == "BUY" else "hold"
    entry_sizing_source = "base_sma" if base.final_signal == "BUY" else "none"
    execution_intent = base.execution_intent
    base_blocked_filters = list(base.trace.get("blocked_filters") or ())
    base_safety_passed = not bool(base_blocked_filters) and str(base.final_reason or "").strip() not in {
        "entry_filter_blocked",
        "blocked",
    }
    fallback_eligible = bool(participation.allowed)
    fallback_block_reason = ""
    if participation_config.fallback_mode == "disabled":
        fallback_eligible = False
        fallback_block_reason = "daily_participation_fallback_disabled"
    elif participation_config.fallback_mode == "requires_base_safety_filter" and not base_safety_passed:
        fallback_eligible = False
        fallback_block_reason = "daily_participation_base_safety_filter_blocked"
    if base.final_signal != "BUY" and fallback_eligible:
        final_signal = "BUY"
        final_reason = participation.reason_code
        entry_signal_source = "daily_participation_fallback"
        entry_sizing_source = "daily_participation_policy"
        execution_intent = EntryExecutionIntent(
            side="BUY",
            intent="enter_open_exposure",
            pair=str(getattr(market, "pair", "") or getattr(count_snapshot, "pair", "") or ""),
            requires_execution_sizing=True,
            budget_fraction_of_cash=float(participation_config.buy_fraction),
            max_budget_krw=float(participation_config.max_order_krw),
        )
    elif base.final_signal != "BUY" and participation.allowed and fallback_block_reason:
        final_signal = "HOLD"
        final_reason = fallback_block_reason
    execution_payload = execution_intent.as_dict() if execution_intent is not None else None
    trace = dict(base.trace)
    trace.update(
        {
            "strategy_family": "daily_participation_sma",
            "base_strategy": "sma_with_filter",
            "strategy_instance_id": str(getattr(count_snapshot, "strategy_instance_id", "") or ""),
            "pair": str(getattr(count_snapshot, "pair", "") or market.pair or ""),
            "entry_signal_source": entry_signal_source,
            "entry_sizing_source": entry_sizing_source,
            "base_entry_signal": base_entry_signal,
            "base_final_reason": base.final_reason,
            "base_blocked_filters": base_blocked_filters,
            "participation_entry_signal": "BUY" if participation.allowed else "HOLD",
            "daily_participation_decision": participation.as_dict(),
            "fallback_mode": participation_config.fallback_mode,
            "fallback_block_reason": fallback_block_reason,
            "timezone": participation_config.timezone,
            "count_basis": participation.count_basis,
            "kst_day": participation.kst_day,
            "daily_count_snapshot_hash": participation.daily_count_snapshot_hash,
            "daily_count_snapshot_event_set_hash": str(getattr(count_snapshot, "event_set_hash", "") or ""),
            "participation_policy_hash": participation.participation_policy_hash,
            "participation_input_hash": participation.participation_input_hash,
            "participation_decision_hash": participation.participation_decision_hash,
            "fail_closed_reason": participation.fail_closed_reason,
            "not_a_fill_guarantee": True,
            "execution_intent": execution_payload,
            "daily_count_snapshot": count_snapshot.as_dict() if hasattr(count_snapshot, "as_dict") else {},
        }
    )
    policy_input_hash = _stable_hash(
        {
            "base_policy_input_hash": base.policy_input_hash,
            "entry_signal_source": entry_signal_source,
            "entry_sizing_source": entry_sizing_source,
            "fallback_mode": participation_config.fallback_mode,
            "daily_count_snapshot_hash": participation.daily_count_snapshot_hash,
            "participation_policy_hash": participation.participation_policy_hash,
            "participation_input_hash": participation.participation_input_hash,
            "execution_sizing": {
                "participation_buy_fraction": float(participation_config.buy_fraction),
                "participation_max_order_krw": float(participation_config.max_order_krw),
            },
        }
    )
    policy_decision_hash = _stable_hash(
        {
            "strategy_name": "daily_participation_sma",
            "final_signal": final_signal,
            "final_reason": final_reason,
            "entry_signal_source": entry_signal_source,
            "entry_sizing_source": entry_sizing_source,
            "fallback_mode": participation_config.fallback_mode,
            "execution_intent": execution_payload,
            "participation_decision_hash": participation.participation_decision_hash,
        }
    )
    return StrategyDecisionV2(
        strategy_name="daily_participation_sma",
        raw_signal=base.raw_signal,
        raw_reason=base.raw_reason,
        entry_signal=base.entry_signal,
        entry_reason=base.entry_reason,
        exit_signal=base.exit_signal,
        exit_reason=base.exit_reason,
        final_signal=final_signal,
        final_reason=final_reason,
        blocked_filters=tuple(base.blocked_filters),
        entry_blocked=base.entry_blocked,
        entry_block_reason=base.entry_block_reason,
        exit_rule=base.exit_rule,
        exit_evaluations=tuple(dict(item) for item in base.exit_evaluations),
        protective_exit_overrode_entry=base.protective_exit_overrode_entry,
        exit_filter_suppression_prevented=base.exit_filter_suppression_prevented,
        position_snapshot=base.position_snapshot,
        execution_intent=execution_intent,
        entry_decision=base.entry_decision,
        trace=trace,
        policy_hash=_stable_hash(trace),
        policy_contract_hash=base.policy_contract_hash,
        policy_input_hash=policy_input_hash,
        policy_decision_hash=policy_decision_hash,
    )


def research_policy_decision_builder(
    *,
    event: Any,
    dataset: DatasetSnapshot,
    candle_index: int,
    position: PositionSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    active_exit_policy: dict[str, Any],
    buy_fraction: float = 0.0,
    materialization_mode: MaterializationMode | str = MaterializationMode.RESEARCH_EXPLORATORY,
    candidate_regime_policy: dict[str, object] | None = None,
    candidate_regime_policy_enforced: bool | None = None,
    decision_records: tuple[dict[str, Any], ...] = (),
    trade_records: tuple[dict[str, Any], ...] = (),
) -> Any:
    daily_values = materialize_strategy_parameters(
        "daily_participation_sma",
        dict(parameter_values),
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )
    base_parameter_values = {
        key: value
        for key, value in dict(daily_values).items()
        if key in SMA_WITH_FILTER_SPEC.accepted_parameter_names
    }
    projector = SmaWithFilterSnapshotProjector()
    projected = projector.project_from_research_event(
        event=event,
        dataset=dataset,
        candle_index=candle_index,
        position=position,
        parameter_values=base_parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        active_exit_policy=active_exit_policy,
        buy_fraction=buy_fraction,
        materialization_mode=materialization_mode,
        candidate_regime_policy=candidate_regime_policy,
        candidate_regime_policy_enforced=candidate_regime_policy_enforced,
    )
    if projected is None:
        return None
    participation_config = daily_participation_config_from_parameters(dict(daily_values))
    count_snapshot = build_research_daily_count_snapshot(
        config=participation_config,
        decision_ts=int(event.decision_ts),
        decision_records=tuple(decision_records),
        trade_records=tuple(trade_records),
        pair=str(dataset.market),
        strategy_instance_id="daily_participation_sma:research_promotion",
        strategy_name="daily_participation_sma",
    )
    mode = (
        materialization_mode
        if isinstance(materialization_mode, MaterializationMode)
        else MaterializationMode(str(materialization_mode))
    )
    if mode.runtime_comparable:
        require_runtime_comparable_daily_count_snapshot(count_snapshot)
    participation_state = count_snapshot.state_snapshot(
        decision_ts=int(event.decision_ts),
        position_open=bool(position.in_position or position.has_executable_exposure),
        entry_allowed=bool(position.entry_allowed),
        market_open=True,
    )
    composite_config = DailyParticipationSmaDecisionConfig(
        base_config=projected.bundle.config,
        participation_config=participation_config,
        participation_state=participation_state,
        count_snapshot=count_snapshot,
    )
    daily_bundle = StrategyDecisionInputBundle.build(
        strategy_name="daily_participation_sma",
        market=projected.bundle.market,
        position=position,
        config=composite_config,
        execution_constraints=projected.bundle.execution_constraints,
        exit_policy_config=projected.bundle.exit_policy_config,
        materialized_parameters_hash=materialized_strategy_parameters_hash(dict(daily_values)),
        snapshot_projector_version="daily_participation_sma_research_projector.v1",
        snapshot_projector_hash=_stable_hash(
            {
                "projector": "daily_participation_sma_research_projector.v1",
                "base_projector_hash": projected.bundle.snapshot_projector_hash,
            }
        ),
        provenance={
            **dict(projected.bundle.provenance),
            "candle_ts": int(event.candle_ts),
            "daily_count_snapshot_source": count_snapshot.source,
        },
        component_payloads={
            "market": projected.bundle.component_payloads.get("market", {})
            if projected.bundle.component_payloads
            else projected.bundle.market.policy_input_payload(),
            "market_feature": projected.bundle.component_payloads.get("market_feature", {})
            if projected.bundle.component_payloads
            else projected.bundle.market.policy_input_payload(),
            "position": position.policy_input_payload(),
            "config": composite_config.policy_input_payload(),
            "execution_constraints": projected.bundle.execution_constraints.policy_input_payload(),
            "exit_policy_config": projected.bundle.component_payloads.get("exit_policy_config", {})
            if projected.bundle.component_payloads
            else {},
        },
    )
    result = StrategyDecisionService().evaluate(
        StrategyEvaluationRequest(
            strategy_name="daily_participation_sma",
            strategy_instance_id="daily_participation_sma:research_promotion",
            mode=mode.value,
            strategy_policy=DailyParticipationSmaStrategy(),
            market_snapshot=daily_bundle.market,
            position_snapshot=daily_bundle.position,
            strategy_config=daily_bundle.config,
            execution_constraints=daily_bundle.execution_constraints,
            exit_policy_config=daily_bundle.exit_policy_config,
            rule_sources=projected.rule_sources,
            approved_profile_hash=(
                candidate_regime_policy.get("strategy_profile_hash")
                if isinstance(candidate_regime_policy, dict)
                else None
            ),
            runtime_contract_hash=None,
            plugin_contract_hash=None,
            request_hash=None,
            provenance={
                "snapshot_builder": "DailyParticipationSmaResearchPolicyDecisionBuilder",
                "base_snapshot_builder": "SmaWithFilterSnapshotProjector",
                "candle_ts": int(event.candle_ts),
                "strategy_parameters_hash": materialized_strategy_parameters_hash(dict(daily_values)),
                "replay_fingerprint": projected.replay_fingerprint,
                "daily_count_snapshot_source": count_snapshot.source,
                "approved_profile_hash_unavailable_reason": "research_candidate_profile_not_supplied"
                if not (
                    isinstance(candidate_regime_policy, dict)
                    and candidate_regime_policy.get("strategy_profile_hash")
                )
                else "",
                "plugin_contract_hash_unavailable_reason": "research_policy_decision_builder_no_plugin_contract",
                "runtime_contract_hash_unavailable_reason": "research_policy_decision_builder_no_runtime_contract",
                "runtime_decision_request_hash_unavailable_reason": "research_policy_decision_builder_no_runtime_request",
            },
            decision_input_bundle=daily_bundle,
            decision_evidence_contract=DAILY_PARTICIPATION_DECISION_EVIDENCE_CONTRACT,
        )
    )
    result.decision.trace.update(
        {
            "parameter_sources": dict(projected.materialized.sources),
            "runtime_comparable": bool(projected.materialized.runtime_comparable),
            "materialization_mode": projected.materialized.mode.value,
            "policy_materialization_mode": projected.materialized.mode.value,
            "legacy_defaults_used": list(projected.materialized.legacy_defaults_used),
            "daily_count_snapshot": count_snapshot.as_dict(),
        }
    )
    return result.decision


def build_runtime_replay_strategy(
    profile: dict[str, Any],
    candidate_regime_policy: dict[str, Any] | None = None,
) -> Any:
    from bithumb_bot.research.sma_with_filter_plugin import build_runtime_replay_strategy as build_base

    params = profile.get("strategy_parameters") if isinstance(profile.get("strategy_parameters"), dict) else {}
    daily_values = materialize_strategy_parameters(
        "daily_participation_sma",
        dict(params),
        fee_rate=0.0,
        slippage_bps=0.0,
    )
    base_profile = dict(profile)
    base_profile["strategy_name"] = "sma_with_filter"
    base_profile["strategy_parameters"] = {
        key: value for key, value in dict(daily_values).items() if key in SMA_WITH_FILTER_SPEC.accepted_parameter_names
    }
    base_strategy = build_base(base_profile, candidate_regime_policy)
    participation_config = daily_participation_config_from_parameters(dict(daily_values))
    return DailyParticipationRuntimeReplayStrategy(
        base_strategy=base_strategy,
        participation_config=participation_config,
        profile=dict(profile),
    )


class DailyParticipationRuntimeReplayStrategy:
    def __init__(
        self,
        *,
        base_strategy: Any,
        participation_config: DailyParticipationPolicyConfig,
        profile: dict[str, Any],
    ) -> None:
        self.base_strategy = base_strategy
        self.participation_config = participation_config
        self.profile = dict(profile)

    @property
    def name(self) -> str:
        return "daily_participation_sma"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_strategy, name)

    def decide_runtime_snapshot(
        self,
        conn: Any,
        *,
        through_ts_ms: int | None = None,
    ) -> Any:
        base_result = self.base_strategy.decide_runtime_snapshot(conn, through_ts_ms=through_ts_ms)
        if base_result is None:
            return None
        pair = str(self.profile.get("market") or getattr(getattr(self.base_strategy, "strategy", None), "pair", "") or "")
        decision_ts = int(getattr(base_result, "candle_ts", 0) or through_ts_ms or 0)
        count_snapshot = build_runtime_daily_count_snapshot_from_sqlite(
            conn=conn,
            config=self.participation_config,
            decision_ts=decision_ts,
            pair=pair,
            strategy_instance_id=str(self.profile.get("strategy_instance_id") or "daily_participation_sma:runtime_replay"),
            strategy_name="daily_participation_sma",
        )
        require_runtime_comparable_daily_count_snapshot(count_snapshot)
        return _daily_runtime_result_from_base(
            base_result=base_result,
            participation_config=self.participation_config,
            count_snapshot=count_snapshot,
            decision_ts=decision_ts,
        )


def _daily_runtime_result_from_base(
    *,
    base_result: Any,
    participation_config: DailyParticipationPolicyConfig,
    count_snapshot: Any,
    decision_ts: int,
    provenance_extra: dict[str, object] | None = None,
) -> Any:
    base_decision = base_result.decision
    position = base_decision.position_snapshot
    pair = str(count_snapshot.pair or base_result.base_context.get("pair") or "")
    participation_state = count_snapshot.state_snapshot(
        decision_ts=int(decision_ts),
        position_open=bool(position.in_position or position.has_executable_exposure),
        entry_allowed=bool(position.entry_allowed),
        market_open=True,
    )
    composite_config = DailyParticipationSmaDecisionConfig(
        base_config=None,
        participation_config=participation_config,
        participation_state=participation_state,
        count_snapshot=count_snapshot,
        base_decision=base_decision,
    )
    runtime_market = _RuntimeDailyMarket(pair=pair)
    component_payloads = {
        "market": runtime_market.policy_input_payload(),
        "market_feature": {
            "base_context": dict(base_result.base_context),
            "base_replay_fingerprint": dict(base_result.replay_fingerprint),
        },
        "position": position.policy_input_payload(),
        "config": composite_config.policy_input_payload(),
        "execution_constraints": {"schema_version": 1, "source": "base_sma_runtime_result"},
        "exit_policy_config": {},
    }
    strategy_parameters_hash = str(
        (provenance_extra or {}).get("strategy_parameters_hash")
        or base_result.base_context.get("strategy_parameters_hash")
        or _stable_hash({"runtime_daily_participation_parameters": participation_config.policy_payload()})
    )
    daily_bundle = StrategyDecisionInputBundle.build(
        strategy_name="daily_participation_sma",
        market=runtime_market,
        position=position,
        config=composite_config,
        execution_constraints=ExecutionConstraintSnapshot(fee_rate_for_decision=0.0),
        exit_policy_config=None,
        materialized_parameters_hash=strategy_parameters_hash,
        snapshot_projector_version="sma_with_filter_snapshot_projector_v1",
        snapshot_projector_hash=_stable_hash({"snapshot_projector": "daily_participation_sma_runtime_feature_snapshot_v1"}),
        provenance=dict(provenance_extra or {}),
        component_payloads=component_payloads,
    )
    request_provenance = {
        **dict(base_result.base_context),
        "snapshot_builder": "DailyParticipationSmaRuntimeFeatureSnapshotAdapter",
        "base_snapshot_builder": "SmaWithFilterRuntimeFeatureSnapshotAdapter",
        "strategy_parameters_hash": strategy_parameters_hash,
        "strategy_instance_id": str(count_snapshot.strategy_instance_id or ""),
        "pair": pair,
        "replay_fingerprint": dict(base_result.replay_fingerprint),
        "plugin_contract_hash_unavailable_reason": "daily_runtime_base_result_direct_call"
        if not base_result.base_context.get("plugin_contract_hash")
        else "",
        "runtime_contract_hash_unavailable_reason": "daily_runtime_base_result_direct_call"
        if not base_result.base_context.get("runtime_contract_hash")
        else "",
        "runtime_decision_request_hash_unavailable_reason": "daily_runtime_base_result_direct_call"
        if not base_result.base_context.get("runtime_decision_request_hash")
        else "",
        "approved_profile_hash_unavailable_reason": "daily_runtime_base_result_no_approved_profile_hash"
        if not base_result.base_context.get("approved_profile_hash")
        else "",
    }
    if provenance_extra:
        request_provenance.update({key: value for key, value in provenance_extra.items() if str(value or "").strip()})
    result = StrategyDecisionService().evaluate(
        StrategyEvaluationRequest(
            strategy_name="daily_participation_sma",
            strategy_instance_id=str(count_snapshot.strategy_instance_id or ""),
            mode="runtime_replay",
            strategy_policy=DailyParticipationSmaStrategy(),
            market_snapshot=daily_bundle.market,
            position_snapshot=daily_bundle.position,
            strategy_config=daily_bundle.config,
            execution_constraints=daily_bundle.execution_constraints,
            exit_policy_config=daily_bundle.exit_policy_config,
            rule_sources={},
            approved_profile_hash=str(base_result.base_context.get("approved_profile_hash") or "") or None,
            runtime_contract_hash=str(base_result.base_context.get("runtime_contract_hash") or "") or None,
            plugin_contract_hash=str(base_result.base_context.get("plugin_contract_hash") or "") or None,
            request_hash=str(base_result.base_context.get("runtime_decision_request_hash") or "") or None,
            provenance=request_provenance,
            decision_input_bundle=daily_bundle,
            decision_evidence_contract=DAILY_PARTICIPATION_DECISION_EVIDENCE_CONTRACT,
        )
    )
    daily_decision = result.decision
    replay_fingerprint = dict(result.replay_fingerprint)
    base_context = dict(base_result.base_context)
    base_context.update(
        {
            "strategy": "daily_participation_sma",
            "strategy_instance_id": str(count_snapshot.strategy_instance_id or ""),
            "pure_policy_hash": daily_decision.policy_hash,
            "policy_input_hash": daily_decision.policy_input_hash,
            "policy_decision_hash": daily_decision.policy_decision_hash,
            "pure_policy_trace": daily_decision.as_trace(),
            "replay_fingerprint": replay_fingerprint,
            "count_basis": participation_config.count_basis,
            "kst_day": count_snapshot.kst_day,
            "daily_count_snapshot_hash": daily_decision.trace.get("daily_count_snapshot_hash"),
            "daily_count_snapshot_event_set_hash": str(count_snapshot.event_set_hash or ""),
            "participation_policy_hash": daily_decision.trace.get("participation_policy_hash"),
            "participation_input_hash": daily_decision.trace.get("participation_input_hash"),
            "participation_decision_hash": daily_decision.trace.get("participation_decision_hash"),
            "fallback_mode": participation_config.fallback_mode,
            "daily_count_snapshot": count_snapshot.as_dict(),
            "strategy_evaluation_receipt": dict(result.receipt),
            "strategy_evaluation_provenance": dict(result.provenance),
            "replay_fingerprint_hash": result.replay_fingerprint_hash,
        }
    )
    return replace(
        base_result,
        decision=daily_decision,
        base_context=base_context,
        replay_fingerprint=replay_fingerprint,
    )


def runtime_feature_snapshot_builder(*, conn: Any, request: Any, feature_snapshot: Any) -> Any:
    strategy_name = str(getattr(request, "strategy_name", "") or "").strip().lower()
    if strategy_name != "daily_participation_sma":
        return feature_snapshot
    profile_params = getattr(request, "parameters", None) or getattr(request, "strategy_parameters", None)
    params = dict(profile_params) if isinstance(profile_params, Mapping) else {}
    base_params = {
        key: value
        for key, value in dict(params).items()
        if key in SMA_WITH_FILTER_SPEC.accepted_parameter_names
    }
    try:
        base_request = replace(
            request,
            strategy_name="sma_with_filter",
            parameters=base_params,
            parameters_raw=base_params,
            parameters_materialized=base_params,
        )
    except TypeError:
        base_request = request
    feature_snapshot = build_sma_with_filter_runtime_feature_snapshot(
        conn=conn,
        request=base_request,
        feature_snapshot=feature_snapshot,
    )
    if feature_snapshot is None:
        return None
    try:
        config = daily_participation_config_from_parameters(materialize_strategy_parameters(
            "daily_participation_sma",
            params,
            fee_rate=0.0,
            slippage_bps=0.0,
        ))
    except Exception:
        return None
    through_ts = getattr(request, "through_ts_ms", None)
    decision_ts = int(through_ts) if through_ts is not None else int(feature_snapshot.payload.get("decision_candle_ts") or 0)
    count_snapshot = build_runtime_daily_count_snapshot_from_sqlite(
        conn=conn,
        config=config,
        decision_ts=decision_ts,
        pair=str(getattr(request, "pair", "") or feature_snapshot.payload.get("pair") or ""),
        strategy_instance_id=str(getattr(request, "strategy_instance_id", "") or ""),
        strategy_name="daily_participation_sma",
    )
    if count_snapshot.snapshot_hash == "sha256:missing":
        return None
    payload = feature_snapshot.as_dict()
    feature_payload = dict(payload.get("feature_payload") or {})
    feature_payload["daily_participation_count_snapshot"] = count_snapshot.as_dict()
    feature_payload["daily_count_snapshot_hash"] = count_snapshot.snapshot_hash
    feature_payload["count_basis"] = config.count_basis
    feature_payload["kst_day"] = count_snapshot.kst_day
    payload["feature_payload"] = feature_payload
    payload["daily_count_snapshot_hash"] = count_snapshot.snapshot_hash
    payload["daily_participation_count_snapshot"] = count_snapshot.as_dict()
    payload["feature_snapshot_hash"] = _stable_hash(payload)
    from bithumb_bot.runtime_data_provider import RuntimeFeatureSnapshot

    return RuntimeFeatureSnapshot(payload)


def exit_policy_materializer(strategy_name: str, parameter_values: dict[str, Any]) -> dict[str, object]:
    if str(strategy_name or "").strip().lower() != "daily_participation_sma":
        raise ValueError(f"daily_participation_sma_exit_policy_strategy_mismatch:{strategy_name}")
    from bithumb_bot.strategy_plugins.sma_with_filter_assembly import SmaWithFilterPolicyAssembly

    base_parameters = {
        key: value
        for key, value in dict(parameter_values).items()
        if key in SMA_WITH_FILTER_SPEC.accepted_parameter_names
    }
    materialized = SmaWithFilterPolicyAssembly().materialize_exit_policy(
        "sma_with_filter",
        base_parameters,
        materialization_mode=MaterializationMode.RESEARCH_PROMOTION.value,
    )
    policy = dict(materialized["exit_policy"])
    config = dict(materialized["exit_policy_config"])
    policy["strategy_name"] = "daily_participation_sma"
    config["strategy_name"] = "daily_participation_sma"
    return {
        **materialized,
        "exit_policy": policy,
        "exit_policy_config": config,
        "exit_policy_source": "daily_participation_sma_composed_sma_exit_policy_materializer",
    }


def build_daily_participation_sma_research_events(
    *,
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    execution_timing_policy: ExecutionTimingPolicy,
    portfolio_policy: Any | None = None,
    context: Any | None = None,
) -> tuple[Any, ...]:
    del portfolio_policy, context
    return SmaWithFilterDecisionAdapter(
        parameter_values=parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        timing_policy=execution_timing_policy,
        strategy_name="daily_participation_sma",
    ).build_events(dataset)


def run_daily_participation_sma_backtest(
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    parameter_stability_score: float | None = None,
    execution_model: ExecutionModel | None = None,
    execution_timing_policy: ExecutionTimingPolicy | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    context: BacktestRunContext | None = None,
) -> BacktestRun:
    from bithumb_bot.research.backtest_runner import run_plugin_backtest

    return run_plugin_backtest(
        plugin=DAILY_PARTICIPATION_SMA_PLUGIN,
        dataset=dataset,
        parameter_values=parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        parameter_stability_score=parameter_stability_score,
        execution_model=execution_model,
        execution_timing_policy=execution_timing_policy,
        portfolio_policy=portfolio_policy,
        context=context,
    )


_RESEARCH_PLUGIN = research_plugin_from_event_builder(
    strategy_name="daily_participation_sma",
    spec=DAILY_PARTICIPATION_SMA_SPEC,
    version=DAILY_PARTICIPATION_SMA_SPEC.strategy_version,
    required_data=DAILY_PARTICIPATION_SMA_SPEC.required_data,
    optional_data=DAILY_PARTICIPATION_SMA_SPEC.optional_data,
    build_research_events=build_daily_participation_sma_research_events,
    diagnostics_namespace="daily_participation_sma",
    diagnostics_count_builder=daily_participation_diagnostics_count_builder,
    research_parameter_materializer=materialize_daily_participation_sma_parameters,
)

_PROMOTION_EXTENSION = PromotionGradeStrategyExtension(
    runtime_replay_builder=build_runtime_replay_strategy,
    runtime_parameter_adapter=RuntimeParameterAdapter(
        from_env=runtime_parameters_from_env,
        from_settings=runtime_parameters_from_settings,
        env_keys=(
            "SMA_SHORT",
            "SMA_LONG",
            "SMA_FILTER_GAP_MIN_RATIO",
            "SMA_FILTER_VOL_WINDOW",
            "SMA_FILTER_VOL_MIN_RANGE_RATIO",
            "SMA_FILTER_OVEREXT_LOOKBACK",
            "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO",
            "SMA_MARKET_REGIME_ENABLED",
            "SMA_COST_EDGE_ENABLED",
            "SMA_COST_EDGE_MIN_RATIO",
            "ENTRY_EDGE_BUFFER_RATIO",
            "STRATEGY_MIN_EXPECTED_EDGE_RATIO",
            "STRATEGY_ENTRY_SLIPPAGE_BPS",
            "LIVE_FEE_RATE_ESTIMATE",
            "STRATEGY_EXIT_RULES",
            "STRATEGY_EXIT_STOP_LOSS_RATIO",
            "STRATEGY_EXIT_MAX_HOLDING_MIN",
            "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO",
            "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO",
            "DAILY_PARTICIPATION_ENABLED",
            "DAILY_PARTICIPATION_TIMEZONE",
            "DAILY_PARTICIPATION_COUNT_BASIS",
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST",
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST",
            "DAILY_PARTICIPATION_BUY_FRACTION",
            "DAILY_PARTICIPATION_MAX_ORDER_KRW",
            "DAILY_PARTICIPATION_FALLBACK_MODE",
        ),
    ),
    research_policy_decision_builder=research_policy_decision_builder,
    exit_policy_materializer=exit_policy_materializer,
    runtime_decision_adapter_factory=runtime_decision_adapter_factory,
    runtime_feature_snapshot_builder=runtime_feature_snapshot_builder,
    policy_assembly_factory=policy_assembly_factory,
    live_dry_run_allowed=True,
    live_real_order_allowed=True,
    approved_profile_required=True,
    fail_closed_reason="daily_participation_sma_capability_missing",
    decision_evidence_contract=DAILY_PARTICIPATION_DECISION_EVIDENCE_CONTRACT,
    runtime_data_requirement_builder=sma_runtime_data_requirements,
)

DAILY_PARTICIPATION_SMA_PLUGIN = build_live_eligible_strategy_plugin(
    research=_RESEARCH_PLUGIN,
    extension=_PROMOTION_EXTENSION,
    runner=run_daily_participation_sma_backtest,
).to_research_strategy_plugin()
