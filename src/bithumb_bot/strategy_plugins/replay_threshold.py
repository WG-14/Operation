from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bithumb_bot.research.strategy_spec import StrategyParameterSchema, StrategySpec
from bithumb_bot.strategy_authoring import (
    ReplayCompatibleStrategyExtension,
    build_replay_compatible_strategy_plugin,
    research_plugin_from_decide_snapshot,
)
from bithumb_bot.strategy_evidence import StrategyDecisionEvidenceBuilder


REPLAY_THRESHOLD_STRATEGY_NAME = "replay_threshold"
REPLAY_THRESHOLD_POLICY_VERSION = "replay_threshold.policy.v1"


REPLAY_THRESHOLD_SPEC = StrategySpec(
    strategy_name=REPLAY_THRESHOLD_STRATEGY_NAME,
    strategy_version="replay_threshold.replay_contract.v1",
    accepted_parameter_names=("REPLAY_THRESHOLD_CLOSE_ABOVE",),
    required_parameter_names=("REPLAY_THRESHOLD_CLOSE_ABOVE",),
    behavior_affecting_parameter_names=("REPLAY_THRESHOLD_CLOSE_ABOVE",),
    metadata_only_parameter_names=(),
    research_only_parameter_names=(),
    default_parameters={},
    decision_contract_version="replay_threshold_decision_contract.v1",
    required_data=("candles",),
    optional_data=(),
    exit_policy_schema={
        "schema_version": 1,
        "rules": (),
        "description": "Replay-compatible threshold example; not live eligible.",
    },
    parameter_schema=(
        StrategyParameterSchema(
            name="REPLAY_THRESHOLD_CLOSE_ABOVE",
            value_type="float",
            min_value=0.0,
            unit="quote_price",
            required=True,
            runtime_bound=True,
            behavior_affecting=True,
        ),
    ),
)


def _materialize_replay_threshold_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    materialized = {
        "REPLAY_THRESHOLD_CLOSE_ABOVE": float(parameters["REPLAY_THRESHOLD_CLOSE_ABOVE"]),
    }
    REPLAY_THRESHOLD_SPEC.validate_parameters(materialized)
    return materialized


def _decision_material(
    *,
    market: str,
    interval: str,
    candle_ts: int,
    candle_index: int,
    close: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    materialized = _materialize_replay_threshold_parameters(parameters)
    threshold = float(materialized["REPLAY_THRESHOLD_CLOSE_ABOVE"])
    signal = "BUY" if float(close) > threshold else "HOLD"
    reason = "replay_threshold_close_above" if signal == "BUY" else "replay_threshold_not_met"
    policy_contract = {
        "schema_version": 1,
        "strategy_name": REPLAY_THRESHOLD_STRATEGY_NAME,
        "policy_contract_version": REPLAY_THRESHOLD_POLICY_VERSION,
        "live_dry_run_allowed": False,
        "live_real_order_allowed": False,
    }
    policy_input = {
        "schema_version": 1,
        "strategy_name": REPLAY_THRESHOLD_STRATEGY_NAME,
        "market": market,
        "interval": interval,
        "candle_ts": int(candle_ts),
        "candle_index": int(candle_index),
        "close": float(close),
        "parameters": dict(materialized),
    }
    policy_decision = {
        "schema_version": 1,
        "final_signal": signal,
        "final_reason": reason,
        "read_only_replay": True,
    }
    evidence = StrategyDecisionEvidenceBuilder().build(
        strategy_name=REPLAY_THRESHOLD_STRATEGY_NAME,
        policy_contract_material=policy_contract,
        policy_input_material=policy_input,
        policy_decision_material=policy_decision,
        replay_fingerprint_material={
            "policy_contract_version": REPLAY_THRESHOLD_POLICY_VERSION,
            "market": market,
            "interval": interval,
            "candle_ts": int(candle_ts),
            "candle_index": int(candle_index),
            "close": float(close),
            "parameters": dict(materialized),
            "read_only_replay": True,
        },
        mode="runtime_replay",
    )
    return {
        "signal": signal,
        "reason": reason,
        "parameters": materialized,
        "policy_contract": policy_contract,
        "policy_input": policy_input,
        "policy_decision": policy_decision,
        "evidence": evidence,
    }


def decide_replay_threshold_snapshot(
    *,
    candle: Any,
    candle_index: int,
    dataset: Any,
    parameter_values: dict[str, Any],
) -> dict[str, Any]:
    material = _decision_material(
        market=str(dataset.market),
        interval=str(dataset.interval),
        candle_ts=int(candle.ts),
        candle_index=int(candle_index),
        close=float(candle.close),
        parameters=parameter_values,
    )
    evidence = material["evidence"]
    return {
        "signal": material["signal"],
        "reason": material["reason"],
        "feature_snapshot": {
            "candle_index": int(candle_index),
            "close": float(candle.close),
            "threshold_close_above": float(material["parameters"]["REPLAY_THRESHOLD_CLOSE_ABOVE"]),
        },
        "strategy_diagnostics": {
            "schema_version": 1,
            "policy_contract_hash": evidence.policy_contract_hash,
            "policy_input_hash": evidence.policy_input_hash,
            "policy_decision_hash": evidence.policy_decision_hash,
            "replay_fingerprint_hash": evidence.replay_fingerprint_hash,
        },
        "extra_payload": {
            "policy_contract_hash": evidence.policy_contract_hash,
            "policy_input_hash": evidence.policy_input_hash,
            "policy_decision_hash": evidence.policy_decision_hash,
            "pure_policy_hash": evidence.policy_hash,
            "replay_fingerprint": dict(evidence.replay_fingerprint),
            "replay_fingerprint_hash": evidence.replay_fingerprint_hash,
        },
    }


@dataclass(frozen=True)
class ReplayThresholdRuntimeReplayStrategy:
    name: str = REPLAY_THRESHOLD_STRATEGY_NAME
    market: str = ""
    interval: str = ""
    parameters: dict[str, Any] | None = None

    def decide(self, conn: Any, *, through_ts_ms: int) -> Any | None:
        from bithumb_bot.strategy.base import StrategyDecision

        query = "SELECT ts, close FROM candles WHERE pair=? AND interval=? AND ts<=? ORDER BY ts DESC LIMIT 1"
        row = conn.execute(query, (self.market, self.interval, int(through_ts_ms))).fetchone()
        if row is None:
            return None
        candle_ts = int(row["ts"]) if hasattr(row, "keys") else int(row[0])
        close = float(row["close"]) if hasattr(row, "keys") else float(row[1])
        count_row = conn.execute(
            "SELECT COUNT(*) FROM candles WHERE pair=? AND interval=? AND ts<=?",
            (self.market, self.interval, candle_ts),
        ).fetchone()
        candle_index = int(count_row[0]) - 1 if count_row is not None else 0
        material = _decision_material(
            market=self.market,
            interval=self.interval,
            candle_ts=candle_ts,
            candle_index=max(0, candle_index),
            close=close,
            parameters=dict(self.parameters or {}),
        )
        evidence = material["evidence"]
        context = {
            "strategy": self.name,
            "final_signal": material["signal"],
            "final_reason": material["reason"],
            "raw_signal": material["signal"],
            "raw_reason": material["reason"],
            "ts": candle_ts,
            "last_close": close,
            "policy_contract_hash": evidence.policy_contract_hash,
            "policy_input_hash": evidence.policy_input_hash,
            "policy_decision_hash": evidence.policy_decision_hash,
            "pure_policy_hash": evidence.policy_hash,
            "replay_fingerprint": dict(evidence.replay_fingerprint),
            "replay_fingerprint_hash": evidence.replay_fingerprint_hash,
            "strategy_evaluation_provenance": dict(evidence.strategy_evaluation_provenance),
            "read_only_replay": True,
        }
        return StrategyDecision(
            signal=str(material["signal"]),
            reason=str(material["reason"]),
            context=context,
        )


def _build_replay_threshold_strategy(
    profile: dict[str, Any],
    candidate_regime_policy: dict[str, Any] | None = None,
) -> ReplayThresholdRuntimeReplayStrategy:
    del candidate_regime_policy
    params = profile.get("strategy_parameters") if isinstance(profile.get("strategy_parameters"), dict) else {}
    return ReplayThresholdRuntimeReplayStrategy(
        market=str(profile.get("market") or ""),
        interval=str(profile.get("interval") or ""),
        parameters=_materialize_replay_threshold_parameters(dict(params)),
    )


_REPLAY_THRESHOLD_RESEARCH_PLUGIN = research_plugin_from_decide_snapshot(
    strategy_name=REPLAY_THRESHOLD_SPEC.strategy_name,
    version=REPLAY_THRESHOLD_SPEC.strategy_version,
    spec=REPLAY_THRESHOLD_SPEC,
    required_data=REPLAY_THRESHOLD_SPEC.required_data,
    optional_data=REPLAY_THRESHOLD_SPEC.optional_data,
    decide_snapshot=decide_replay_threshold_snapshot,
    diagnostics_namespace=REPLAY_THRESHOLD_STRATEGY_NAME,
)


REPLAY_THRESHOLD_PLUGIN = build_replay_compatible_strategy_plugin(
    research=_REPLAY_THRESHOLD_RESEARCH_PLUGIN,
    extension=ReplayCompatibleStrategyExtension(
        runtime_replay_builder=_build_replay_threshold_strategy,
        parameter_materializer=_materialize_replay_threshold_parameters,
    ),
).to_research_strategy_plugin()
