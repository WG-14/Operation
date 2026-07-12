"""Built-in runtime plugins.  No object here imports the research package."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bithumb_bot.runtime_adapters.safe_hold import SafeHoldRuntimeDecisionAdapter
from bithumb_bot.runtime_adapters.sma_with_filter import SmaWithFilterRuntimeDecisionAdapter
from bithumb_bot.runtime_sma_snapshot import decide_sma_with_filter_runtime_snapshot_from_db
from bithumb_bot.strategy_evidence_contract import DecisionEvidenceContract, GENERIC_DECISION_EVIDENCE_CONTRACT
from bithumb_bot.strategy_plugins.sma_with_filter_assembly import MaterializationMode, SmaWithFilterPolicyAssembly

from .capabilities import DataCapabilityRequirement, OperationStrategyDataRequirements, RuntimeParameterAdapter, StrategyRuntimeCapabilities
from .plugin import OperationStrategyPlugin
from .spec import SMA_WITH_FILTER_SPEC, StrategyParameterSchema, StrategySpec, materialize_strategy_parameters


SMA_DECISION_EVIDENCE_CONTRACT = DecisionEvidenceContract(
    requires_decision_input_bundle=True,
    required_promotion_provenance_fields=("decision_input_bundle_hash", "decision_input_contract_hash", "decision_input_bundle_payload_hash", "market_feature_hash", "final_exit_decision_input_hash", "snapshot_projector_version", "snapshot_projector_hash"),
    required_live_real_order_fields=("decision_input_bundle_hash", "decision_input_contract_hash", "decision_input_bundle_payload_hash", "market_feature_hash", "final_exit_decision_input_hash", "snapshot_projector_version", "snapshot_projector_hash"),
    required_live_real_order_one_of_field_groups=(("fee_authority_hash", "fee_authority_payload_hash"), ("order_rules_hash", "order_rules_payload_hash")),
    snapshot_projector_contract="sma_with_filter_snapshot_projector_v1",
)


def _sma_params_from_env(env: dict[str, str]) -> dict[str, Any]:
    def value(name: str, default: str) -> str: return str(env.get(name, default))
    return {name: value(name, str(default)) for name, default in {"SMA_SHORT": 7, "SMA_LONG": 30, **SMA_WITH_FILTER_SPEC.default_parameters}.items()}


def _sma_params_from_settings(cfg: object) -> dict[str, Any]:
    raw = {name: getattr(cfg, name, default) for name, default in SMA_WITH_FILTER_SPEC.default_parameters.items()}
    raw["SMA_SHORT"] = getattr(cfg, "SMA_SHORT", 7)
    raw["SMA_LONG"] = getattr(cfg, "SMA_LONG", 30)
    return materialize_strategy_parameters("sma_with_filter", raw)


def _sma_requirements(spec: object | None) -> OperationStrategyDataRequirements:
    params = dict(getattr(spec, "parameters", {}) or {})
    lookback = max(int(params.get("SMA_LONG", 30)) + 2, int(params.get("SMA_FILTER_VOL_WINDOW", 10)), int(params.get("SMA_FILTER_OVEREXT_LOOKBACK", 3)) + 1)
    return OperationStrategyDataRequirements(required_data=("candles",), optional_data=("top_of_book",), capabilities=(DataCapabilityRequirement(name="candles", required=True, min_coverage_pct=100.0, lookback_rows=lookback, closed_candle_required=True, source="sqlite_candles", evidence_level="closed_candle_lookback"), DataCapabilityRequirement(name="top_of_book", required=False)))


@dataclass(frozen=True)
class _SmaReplay:
    strategy: Any
    def decide_runtime_snapshot(self, conn: Any, *, through_ts_ms: int | None = None) -> Any:
        return decide_sma_with_filter_runtime_snapshot_from_db(conn, self.strategy, through_ts_ms=through_ts_ms)
    @property
    def name(self) -> str: return str(self.strategy.name)


def _sma_replay(profile: dict[str, Any], candidate_regime_policy: dict[str, Any] | None = None) -> _SmaReplay:
    params = dict(profile.get("strategy_parameters") or {})
    assembly = SmaWithFilterPolicyAssembly()
    materialized = assembly.materialize_parameters(params, MaterializationMode.RUNTIME_REPLAY, profile=profile)
    return _SmaReplay(assembly.build_strategy(materialized, pair=str(profile.get("market") or ""), interval=str(profile.get("interval") or ""), candidate_regime_policy=candidate_regime_policy))


def _sma_exit_policy(strategy_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return SmaWithFilterPolicyAssembly().materialize_exit_policy(strategy_name, parameters, materialization_mode=MaterializationMode.RUNTIME_REPLAY.value)


SAFE_HOLD_SPEC = StrategySpec("safe_hold", "safe_hold.runtime.v1", (), (), (), (), (), {}, "safe_hold_runtime_policy_v1", (), (), {"schema_version": 1, "rules": ()})


def _safe_hold_params(_env_or_cfg: object) -> dict[str, Any]: return {}


def _safe_hold_assembly() -> object: return object()


BUILTIN_OPERATION_STRATEGY_PLUGINS = (
    OperationStrategyPlugin(
        name="sma_with_filter", version="sma_with_filter.operation_runtime.v1", spec=SMA_WITH_FILTER_SPEC,
        required_data=("candles",), optional_data=("top_of_book",),
        runtime_capabilities=StrategyRuntimeCapabilities(True, True, live_dry_run_allowed=True, live_real_order_allowed=True, fail_closed_reason="sma_runtime_contract_missing"),
        runtime_parameter_adapter=RuntimeParameterAdapter(_sma_params_from_env, _sma_params_from_settings, env_keys=tuple(SMA_WITH_FILTER_SPEC.accepted_parameter_names)),
        runtime_decision_adapter_factory=SmaWithFilterRuntimeDecisionAdapter,
        runtime_feature_snapshot_builder=None, runtime_data_requirement_builder=_sma_requirements,
        runtime_replay_builder=_sma_replay, policy_assembly_factory=SmaWithFilterPolicyAssembly,
        exit_policy_materializer=_sma_exit_policy, decision_evidence_contract=SMA_DECISION_EVIDENCE_CONTRACT,
        decision_contract_version=SMA_WITH_FILTER_SPEC.decision_contract_version, diagnostics_namespace="sma_with_filter",
    ),
    OperationStrategyPlugin(
        name="safe_hold", version="safe_hold.runtime.v1", spec=SAFE_HOLD_SPEC,
        runtime_capabilities=StrategyRuntimeCapabilities(False, False, research_only=False, baseline_only=False, live_dry_run_allowed=False, live_real_order_allowed=False, approved_profile_required=False, accepts_empty_runtime_parameters=True, fail_closed_reason="safe_hold_runtime_fallback_not_live_eligible"),
        runtime_parameter_adapter=RuntimeParameterAdapter(_safe_hold_params, _safe_hold_params),
        runtime_decision_adapter_factory=SafeHoldRuntimeDecisionAdapter, runtime_feature_snapshot_builder=None,
        runtime_data_requirement_builder=None, runtime_replay_builder=None, policy_assembly_factory=_safe_hold_assembly,
        exit_policy_materializer=None, decision_evidence_contract=GENERIC_DECISION_EVIDENCE_CONTRACT,
        decision_contract_version="safe_hold_runtime_policy_v1", diagnostics_namespace="safe_hold",
    ),
)
