from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from bithumb_bot.artifact_hashing import sha256_prefixed


class StrategySpecError(ValueError):
    pass


@dataclass(frozen=True)
class StrategyParameterSchema:
    name: str
    value_type: str
    required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    enum: tuple[object, ...] = ()
    unit: str = ""
    runtime_bound: bool = True
    behavior_affecting: bool = True
    deprecated_keys: tuple[str, ...] = ()
    migration_rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.value_type,
            "required": bool(self.required),
            "min": self.min_value,
            "max": self.max_value,
            "enum": list(self.enum),
            "unit": self.unit,
            "runtime_bound": bool(self.runtime_bound),
            "behavior_affecting": bool(self.behavior_affecting),
            "deprecated_keys": list(self.deprecated_keys),
            "migration_rule": self.migration_rule,
        }

    def validate(self, value: object) -> None:
        if self.value_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise StrategySpecError(f"{self.name} must be int")
            comparable: float | str | bool = float(value)
        elif self.value_type == "float":
            try:
                numeric_float = float(value)
            except (TypeError, ValueError) as exc:
                raise StrategySpecError(f"{self.name} must be float") from exc
            if not math.isfinite(numeric_float):
                raise StrategySpecError(f"{self.name} must be finite")
            comparable = numeric_float
        elif self.value_type == "bool":
            if not isinstance(value, bool):
                raise StrategySpecError(f"{self.name} must be bool")
            comparable = value
        elif self.value_type == "str":
            if not isinstance(value, str):
                raise StrategySpecError(f"{self.name} must be str")
            comparable = value
        else:
            raise StrategySpecError(f"{self.name} has unsupported schema type:{self.value_type}")
        if self.enum and value not in self.enum:
            raise StrategySpecError(f"{self.name} must be one of {','.join(map(str, self.enum))}")
        if isinstance(comparable, float):
            if self.min_value is not None and comparable < float(self.min_value):
                raise StrategySpecError(f"{self.name} must be >= {self.min_value}")
            if self.max_value is not None and comparable > float(self.max_value):
                raise StrategySpecError(f"{self.name} must be <= {self.max_value}")


@dataclass(frozen=True)
class StrategySpec:
    strategy_name: str
    strategy_version: str
    accepted_parameter_names: tuple[str, ...]
    required_parameter_names: tuple[str, ...]
    behavior_affecting_parameter_names: tuple[str, ...]
    metadata_only_parameter_names: tuple[str, ...]
    research_only_parameter_names: tuple[str, ...]
    default_parameters: dict[str, Any]
    decision_contract_version: str
    required_data: tuple[str, ...]
    optional_data: tuple[str, ...]
    exit_policy_schema: dict[str, Any]
    parameter_schema: tuple[StrategyParameterSchema, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "accepted_parameter_names": list(self.accepted_parameter_names),
            "required_parameter_names": list(self.required_parameter_names),
            "behavior_affecting_parameter_names": list(self.behavior_affecting_parameter_names),
            "metadata_only_parameter_names": list(self.metadata_only_parameter_names),
            "research_only_parameter_names": list(self.research_only_parameter_names),
            "default_parameters": dict(self.default_parameters),
            "decision_contract_version": self.decision_contract_version,
            "required_data": list(self.required_data),
            "optional_data": list(self.optional_data),
            "exit_policy_schema": dict(self.exit_policy_schema),
            "parameter_schema": [item.as_dict() for item in self.parameter_schema],
        }

    def validate_parameters(self, parameter_values: dict[str, Any]) -> None:
        schemas = {item.name: item for item in self.parameter_schema}
        for schema in schemas.values():
            if schema.required and schema.name not in parameter_values:
                raise StrategySpecError(f"missing required strategy parameter(s): {schema.name}")
        if schemas:
            unknown = sorted(set(parameter_values) - set(self.accepted_parameter_names))
            if unknown:
                raise StrategySpecError(f"unknown strategy parameter(s): {','.join(unknown)}")
        for name, value in parameter_values.items():
            schema = schemas.get(name)
            if schema is not None:
                schema.validate(value)

    def spec_hash(self) -> str:
        return sha256_prefixed(self.as_dict())


SMA_WITH_FILTER_SPEC = StrategySpec(
    strategy_name="sma_with_filter",
    strategy_version="sma_with_filter.research_runtime_contract.v2",
    accepted_parameter_names=(
        "SMA_SHORT", "SMA_LONG", "SMA_FILTER_GAP_MIN_RATIO", "SMA_FILTER_VOL_WINDOW",
        "SMA_FILTER_VOL_MIN_RANGE_RATIO", "SMA_FILTER_VOLUME_WINDOW", "SMA_FILTER_LIQUIDITY_WINDOW",
        "SMA_MARKET_REGIME_ENABLED", "SMA_FILTER_OVEREXT_LOOKBACK", "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO",
        "SMA_COST_EDGE_ENABLED", "SMA_COST_EDGE_MIN_RATIO", "ENTRY_EDGE_BUFFER_RATIO",
        "STRATEGY_MIN_EXPECTED_EDGE_RATIO", "STRATEGY_ENTRY_SLIPPAGE_BPS", "LIVE_FEE_RATE_ESTIMATE",
        "STRATEGY_EXIT_RULES", "STRATEGY_EXIT_STOP_LOSS_RATIO", "STRATEGY_EXIT_MAX_HOLDING_MIN",
        "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO", "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO",
    ),
    required_parameter_names=("SMA_SHORT", "SMA_LONG"),
    behavior_affecting_parameter_names=(
        "SMA_SHORT", "SMA_LONG", "SMA_FILTER_GAP_MIN_RATIO", "SMA_FILTER_VOL_WINDOW",
        "SMA_FILTER_VOL_MIN_RANGE_RATIO", "SMA_FILTER_OVEREXT_LOOKBACK", "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO",
        "SMA_MARKET_REGIME_ENABLED", "SMA_COST_EDGE_ENABLED", "SMA_COST_EDGE_MIN_RATIO",
        "ENTRY_EDGE_BUFFER_RATIO", "STRATEGY_MIN_EXPECTED_EDGE_RATIO", "STRATEGY_ENTRY_SLIPPAGE_BPS",
        "LIVE_FEE_RATE_ESTIMATE", "STRATEGY_EXIT_RULES", "STRATEGY_EXIT_STOP_LOSS_RATIO",
        "STRATEGY_EXIT_MAX_HOLDING_MIN", "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO",
        "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO",
    ),
    metadata_only_parameter_names=(),
    research_only_parameter_names=("SMA_FILTER_VOLUME_WINDOW", "SMA_FILTER_LIQUIDITY_WINDOW"),
    default_parameters={
        "SMA_FILTER_GAP_MIN_RATIO": 0.0012, "SMA_FILTER_VOL_WINDOW": 10,
        "SMA_FILTER_VOL_MIN_RANGE_RATIO": 0.003, "SMA_FILTER_VOLUME_WINDOW": 10,
        "SMA_FILTER_LIQUIDITY_WINDOW": 10, "SMA_FILTER_OVEREXT_LOOKBACK": 3,
        "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO": 0.02, "SMA_MARKET_REGIME_ENABLED": True,
        "SMA_COST_EDGE_ENABLED": True, "SMA_COST_EDGE_MIN_RATIO": 0.0,
        "ENTRY_EDGE_BUFFER_RATIO": 0.0005, "STRATEGY_MIN_EXPECTED_EDGE_RATIO": 0.0,
        "STRATEGY_ENTRY_SLIPPAGE_BPS": 0.0, "LIVE_FEE_RATE_ESTIMATE": 0.0004,
        "STRATEGY_EXIT_RULES": "stop_loss,opposite_cross,max_holding_time",
        "STRATEGY_EXIT_STOP_LOSS_RATIO": 0.0, "STRATEGY_EXIT_MAX_HOLDING_MIN": 0,
        "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO": 0.0,
        "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO": 0.0,
    },
    parameter_schema=(
        StrategyParameterSchema("SMA_SHORT", "int", required=True, min_value=1, unit="candles"),
        StrategyParameterSchema("SMA_LONG", "int", required=True, min_value=1, unit="candles"),
        StrategyParameterSchema("SMA_FILTER_GAP_MIN_RATIO", "float", min_value=0.0, unit="price_ratio"),
        StrategyParameterSchema("SMA_FILTER_VOL_WINDOW", "int", min_value=1, unit="candles"),
        StrategyParameterSchema("SMA_FILTER_VOL_MIN_RANGE_RATIO", "float", min_value=0.0, unit="price_range_ratio"),
        StrategyParameterSchema("SMA_FILTER_VOLUME_WINDOW", "int", min_value=1, unit="candles", runtime_bound=False, behavior_affecting=False),
        StrategyParameterSchema("SMA_FILTER_LIQUIDITY_WINDOW", "int", min_value=1, unit="candles", runtime_bound=False, behavior_affecting=False),
        StrategyParameterSchema("SMA_FILTER_OVEREXT_LOOKBACK", "int", min_value=1, unit="candles"),
        StrategyParameterSchema("SMA_FILTER_OVEREXT_MAX_RETURN_RATIO", "float", min_value=0.0, unit="return_ratio"),
        StrategyParameterSchema("SMA_MARKET_REGIME_ENABLED", "bool", unit="enabled_flag"),
        StrategyParameterSchema("SMA_COST_EDGE_ENABLED", "bool", unit="enabled_flag"),
        StrategyParameterSchema("SMA_COST_EDGE_MIN_RATIO", "float", min_value=0.0, unit="edge_ratio"),
        StrategyParameterSchema("ENTRY_EDGE_BUFFER_RATIO", "float", min_value=0.0, unit="edge_ratio"),
        StrategyParameterSchema("STRATEGY_MIN_EXPECTED_EDGE_RATIO", "float", min_value=0.0, unit="edge_ratio"),
        StrategyParameterSchema("STRATEGY_ENTRY_SLIPPAGE_BPS", "float", min_value=0.0, unit="basis_points"),
        StrategyParameterSchema("LIVE_FEE_RATE_ESTIMATE", "float", min_value=0.0, unit="fee_ratio"),
        StrategyParameterSchema("STRATEGY_EXIT_RULES", "str", unit="comma_separated_exit_rule_names"),
        StrategyParameterSchema("STRATEGY_EXIT_STOP_LOSS_RATIO", "float", min_value=0.0, unit="unrealized_pnl_ratio"),
        StrategyParameterSchema("STRATEGY_EXIT_MAX_HOLDING_MIN", "int", min_value=0, unit="minutes"),
        StrategyParameterSchema("STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO", "float", min_value=0.0, unit="pnl_ratio"),
        StrategyParameterSchema("STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO", "float", min_value=0.0, unit="pnl_ratio"),
    ),
    decision_contract_version="research_sma_decision_contract.v3_entry_exit_risk_exit",
    required_data=("candles",),
    optional_data=("top_of_book",),
    exit_policy_schema={
        "schema_version": 1,
        "rules": ("stop_loss", "opposite_cross", "max_holding_time"),
        "stop_loss": {
            "unit": "unrealized_pnl_ratio", "disabled_value": 0,
            "evaluation_price_basis": "closed_candle_mark", "intrabar_stop_modeled": False,
            "limitation_reasons": ("intra_candle_path_unavailable", "candle_close_stop_may_exit_later_than_real_stop"),
        },
        "max_holding_time": {"unit": "minutes", "disabled_value": 0},
        "opposite_cross": {
            "min_take_profit_ratio": "max(configured, roundtrip_fee)",
            "small_loss_tolerance_ratio": "defer_noise_band",
        },
    },
)


def strategy_spec_for_name(strategy_name: str) -> StrategySpec:
    if strategy_name in {"sma_with_filter", "test_top_of_book_required", "__test_top_of_book_required__"}:
        return SMA_WITH_FILTER_SPEC
    try:
        from .registry import OperationStrategyRegistryError, resolve_operation_strategy_plugin
        return resolve_operation_strategy_plugin(strategy_name).spec
    except OperationStrategyRegistryError as exc:
        raise StrategySpecError(f"unsupported operation strategy: {strategy_name}") from exc


def runtime_bound_behavior_parameter_names_for_spec(strategy_spec: object) -> tuple[str, ...]:
    """Return runtime-bound behavior parameters from a duck-typed spec object."""
    research_only = set(getattr(strategy_spec, "research_only_parameter_names"))
    return tuple(
        name
        for name in getattr(strategy_spec, "behavior_affecting_parameter_names")
        if name not in research_only
    )


def strategy_parameter_source_map_for_spec(
    strategy_spec: object,
    parameter_values: dict[str, Any],
    *,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
) -> dict[str, str]:
    raw = dict(parameter_values)
    accepted_parameter_names = set(getattr(strategy_spec, "accepted_parameter_names"))
    sources = {
        key: "strategy_spec_default"
        for key in dict(getattr(strategy_spec, "default_parameters"))
    }
    for key in raw:
        sources[key] = "raw_parameter_values"
    if (
        fee_rate is not None
        and "LIVE_FEE_RATE_ESTIMATE" in accepted_parameter_names
        and "LIVE_FEE_RATE_ESTIMATE" not in raw
    ):
        sources["LIVE_FEE_RATE_ESTIMATE"] = "cost_model_fee_rate"
    if (
        slippage_bps is not None
        and "STRATEGY_ENTRY_SLIPPAGE_BPS" in accepted_parameter_names
        and "STRATEGY_ENTRY_SLIPPAGE_BPS" not in raw
    ):
        sources["STRATEGY_ENTRY_SLIPPAGE_BPS"] = "cost_model_slippage_bps"
    return sources


def materialize_strategy_parameters_for_spec(
    strategy_spec: object,
    parameter_values: dict[str, Any],
    *,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
) -> dict[str, Any]:
    """Materialize parameters from a duck-typed spec without research type coupling."""
    raw = dict(parameter_values)
    accepted_parameter_names = set(getattr(strategy_spec, "accepted_parameter_names"))
    values = {**dict(getattr(strategy_spec, "default_parameters")), **raw}
    getattr(strategy_spec, "validate_parameters")(values)
    if (
        fee_rate is not None
        and "LIVE_FEE_RATE_ESTIMATE" in accepted_parameter_names
        and "LIVE_FEE_RATE_ESTIMATE" not in raw
    ):
        values["LIVE_FEE_RATE_ESTIMATE"] = float(fee_rate)
    if (
        slippage_bps is not None
        and "STRATEGY_ENTRY_SLIPPAGE_BPS" in accepted_parameter_names
        and "STRATEGY_ENTRY_SLIPPAGE_BPS" not in raw
    ):
        values["STRATEGY_ENTRY_SLIPPAGE_BPS"] = float(slippage_bps)
    _validate_exit_policy_materialized_values(values)
    return values


def runtime_bound_behavior_parameter_names(strategy_name: str) -> tuple[str, ...]:
    return runtime_bound_behavior_parameter_names_for_spec(strategy_spec_for_name(strategy_name))


def strategy_parameter_source_map(
    strategy_name: str,
    parameter_values: dict[str, Any],
    *,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
) -> dict[str, str]:
    return strategy_parameter_source_map_for_spec(
        strategy_spec_for_name(strategy_name),
        parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )


def materialize_strategy_parameters(
    strategy_name: str,
    parameter_values: dict[str, Any],
    *,
    fee_rate: float | None = None,
    slippage_bps: float | None = None,
) -> dict[str, Any]:
    return materialize_strategy_parameters_for_spec(
        strategy_spec_for_name(strategy_name),
        parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )


def materialized_strategy_parameters_hash(parameter_values: dict[str, Any]) -> str:
    return sha256_prefixed(dict(parameter_values))


def exit_policy_hash(policy: dict[str, Any]) -> str:
    return sha256_prefixed(policy)


COMMON_EXIT_RULE_NAMES = frozenset({"stop_loss", "max_holding_time", "take_profit"})


def common_exit_policy_materialization(
    *,
    strategy_name: str,
    parameter_values: dict[str, Any],
    strategy_spec: object,
    materialization_mode: str,
):
    """Operation fallback for common-rule and no-exit plugins.

    Plugin-owned rules deliberately cannot fall through this path; that keeps
    exit authority fail-closed while preserving old common/no-exit payloads.
    """
    from bithumb_bot.artifact_hashing import sha256_prefixed
    from .plugin import ExitPolicyMaterialization

    schema_rules = tuple(str(item).strip().lower() for item in getattr(strategy_spec, "exit_policy_schema", {}).get("rules") or ())
    if not schema_rules:
        policy = {
            "schema_version": 1, "strategy_name": strategy_name, "rules": [],
            "common_rules": [], "strategy_rules": [],
            "entry_exit_policy": "strategy_emits_no_exit_intent",
            "stop_loss": {"enabled": False, "disabled_when_zero": True},
            "max_holding_time": {"enabled": False, "disabled_when_zero": True},
            "take_profit": {"enabled": False, "disabled_when_zero": True},
        }
        config = {"schema_version": 1, "strategy_name": strategy_name, "rules": []}
        source = "default_no_exit_materializer"
    else:
        strategy_owned = sorted(set(schema_rules) - COMMON_EXIT_RULE_NAMES)
        if strategy_owned:
            raise StrategySpecError("strategy exit policy materializer required for strategy-owned rule(s): " + ",".join(strategy_owned))
        values = materialize_strategy_parameters_for_spec(strategy_spec, parameter_values)
        rules = _normalize_exit_rule_names(str(values.get("STRATEGY_EXIT_RULES") or ""))
        stop_loss = float(values.get("STRATEGY_EXIT_STOP_LOSS_RATIO") or 0.0)
        max_holding = int(values.get("STRATEGY_EXIT_MAX_HOLDING_MIN") or 0)
        take_profit = float(values.get("TAKE_PROFIT_RATIO") or 0.0)
        policy = {
            "schema_version": 1, "strategy_name": strategy_name, "rules": list(rules),
            "common_rules": [rule for rule in rules if rule in COMMON_EXIT_RULE_NAMES], "strategy_rules": [],
            "stop_loss": {"enabled": "stop_loss" in rules and stop_loss > 0.0, "stop_loss_ratio": stop_loss, "disabled_when_zero": True, "evaluation_price_basis": "closed_candle_mark", "intrabar_stop_modeled": False, "limitation_reasons": ["intra_candle_path_unavailable", "candle_close_stop_may_exit_later_than_real_stop"]},
            "max_holding_time": {"enabled": "max_holding_time" in rules and max_holding > 0, "max_holding_min": max_holding, "disabled_when_zero": True},
            "take_profit": {"enabled": "take_profit" in rules and take_profit > 0.0, "take_profit_ratio": take_profit, "disabled_when_zero": True, "evaluation_price_basis": "closed_candle_mark"},
            "trailing_stop": {"enabled": False, "trailing_stop_ratio": 0.0, "disabled_when_zero": True, "evaluation_status": "diagnostic_policy_bound_not_runtime_evaluated"},
            "break_even_stop": {"enabled": False, "evaluation_status": "diagnostic_policy_bound_not_runtime_evaluated"},
            "opposite_signal_exit": {"enabled": False, "evaluation_status": "diagnostic_policy_bound_not_runtime_evaluated"},
            "regime_change_exit": {"enabled": False, "evaluation_status": "diagnostic_policy_bound_not_runtime_evaluated"},
        }
        config = {key: value for key, value in policy.items() if key not in {"common_rules", "strategy_rules"}}
        source = "default_common_exit_policy_materializer"
    contract = {"schema_version": 1, "strategy_name": strategy_name, "materializer_module": None, "materializer_qualname": None, "exit_policy_source": source}
    return ExitPolicyMaterialization(policy, sha256_prefixed(policy), sha256_prefixed(contract), config, sha256_prefixed(config), source, materialization_mode)


def _normalize_exit_rule_names(raw: str) -> tuple[str, ...]:
    return tuple(token.strip().lower() for token in raw.split(",") if token.strip())


def _validate_exit_policy_materialized_values(values: dict[str, Any]) -> None:
    stop_loss_ratio = _non_negative_float("STRATEGY_EXIT_STOP_LOSS_RATIO", values.get("STRATEGY_EXIT_STOP_LOSS_RATIO", 0.0))
    take_profit_ratio = _non_negative_float("TAKE_PROFIT_RATIO", values.get("TAKE_PROFIT_RATIO", 0.0))
    _validate_exit_rule_names(values.get("STRATEGY_EXIT_RULES") or "")
    rules = _normalize_exit_rule_names(str(values.get("STRATEGY_EXIT_RULES") or ""))
    if stop_loss_ratio > 0.0 and "stop_loss" not in rules:
        raise StrategySpecError("STRATEGY_EXIT_STOP_LOSS_RATIO is positive but STRATEGY_EXIT_RULES does not include stop_loss")
    if take_profit_ratio > 0.0 and "take_profit" not in rules:
        raise StrategySpecError("TAKE_PROFIT_RATIO is positive but STRATEGY_EXIT_RULES does not include take_profit")


def _non_negative_float(name: str, value: object) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategySpecError(f"{name} must be a finite value >= 0, got {value!r}") from exc
    if not math.isfinite(resolved) or resolved < 0.0:
        raise StrategySpecError(f"{name} must be a finite value >= 0, got {value!r}")
    return resolved


def _validate_exit_rule_names(raw: object) -> None:
    if not isinstance(raw, str):
        raise StrategySpecError("STRATEGY_EXIT_RULES must be str")
    supported = {"stop_loss", "max_holding_time", "take_profit", "opposite_cross"}
    unsupported = sorted(set(_normalize_exit_rule_names(raw)) - supported)
    if unsupported:
        raise StrategySpecError("STRATEGY_EXIT_RULES contains unsupported rule(s): " + ",".join(unsupported))
