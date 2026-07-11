from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

import bithumb_bot.operation_strategy.spec as operation_spec
from bithumb_bot.operation_strategy.spec import (
    SMA_WITH_FILTER_SPEC,
    StrategySpecError,
    exit_policy_hash,
    materialize_strategy_parameters,
    materialized_strategy_parameters_hash,
    runtime_bound_behavior_parameter_names,
    strategy_parameter_source_map,
    strategy_spec_for_name,
)


GOLDEN_SPEC_PAYLOAD = json.loads(
    r'''{"strategy_name":"sma_with_filter","strategy_version":"sma_with_filter.research_runtime_contract.v2","accepted_parameter_names":["SMA_SHORT","SMA_LONG","SMA_FILTER_GAP_MIN_RATIO","SMA_FILTER_VOL_WINDOW","SMA_FILTER_VOL_MIN_RANGE_RATIO","SMA_FILTER_VOLUME_WINDOW","SMA_FILTER_LIQUIDITY_WINDOW","SMA_MARKET_REGIME_ENABLED","SMA_FILTER_OVEREXT_LOOKBACK","SMA_FILTER_OVEREXT_MAX_RETURN_RATIO","SMA_COST_EDGE_ENABLED","SMA_COST_EDGE_MIN_RATIO","ENTRY_EDGE_BUFFER_RATIO","STRATEGY_MIN_EXPECTED_EDGE_RATIO","STRATEGY_ENTRY_SLIPPAGE_BPS","LIVE_FEE_RATE_ESTIMATE","STRATEGY_EXIT_RULES","STRATEGY_EXIT_STOP_LOSS_RATIO","STRATEGY_EXIT_MAX_HOLDING_MIN","STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO","STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO"],"required_parameter_names":["SMA_SHORT","SMA_LONG"],"behavior_affecting_parameter_names":["SMA_SHORT","SMA_LONG","SMA_FILTER_GAP_MIN_RATIO","SMA_FILTER_VOL_WINDOW","SMA_FILTER_VOL_MIN_RANGE_RATIO","SMA_FILTER_OVEREXT_LOOKBACK","SMA_FILTER_OVEREXT_MAX_RETURN_RATIO","SMA_MARKET_REGIME_ENABLED","SMA_COST_EDGE_ENABLED","SMA_COST_EDGE_MIN_RATIO","ENTRY_EDGE_BUFFER_RATIO","STRATEGY_MIN_EXPECTED_EDGE_RATIO","STRATEGY_ENTRY_SLIPPAGE_BPS","LIVE_FEE_RATE_ESTIMATE","STRATEGY_EXIT_RULES","STRATEGY_EXIT_STOP_LOSS_RATIO","STRATEGY_EXIT_MAX_HOLDING_MIN","STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO","STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO"],"metadata_only_parameter_names":[],"research_only_parameter_names":["SMA_FILTER_VOLUME_WINDOW","SMA_FILTER_LIQUIDITY_WINDOW"],"default_parameters":{"SMA_FILTER_GAP_MIN_RATIO":0.0012,"SMA_FILTER_VOL_WINDOW":10,"SMA_FILTER_VOL_MIN_RANGE_RATIO":0.003,"SMA_FILTER_VOLUME_WINDOW":10,"SMA_FILTER_LIQUIDITY_WINDOW":10,"SMA_FILTER_OVEREXT_LOOKBACK":3,"SMA_FILTER_OVEREXT_MAX_RETURN_RATIO":0.02,"SMA_MARKET_REGIME_ENABLED":true,"SMA_COST_EDGE_ENABLED":true,"SMA_COST_EDGE_MIN_RATIO":0.0,"ENTRY_EDGE_BUFFER_RATIO":0.0005,"STRATEGY_MIN_EXPECTED_EDGE_RATIO":0.0,"STRATEGY_ENTRY_SLIPPAGE_BPS":0.0,"LIVE_FEE_RATE_ESTIMATE":0.0004,"STRATEGY_EXIT_RULES":"stop_loss,opposite_cross,max_holding_time","STRATEGY_EXIT_STOP_LOSS_RATIO":0.0,"STRATEGY_EXIT_MAX_HOLDING_MIN":0,"STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO":0.0,"STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO":0.0},"decision_contract_version":"research_sma_decision_contract.v3_entry_exit_risk_exit","required_data":["candles"],"optional_data":["top_of_book"],"exit_policy_schema":{"schema_version":1,"rules":["stop_loss","opposite_cross","max_holding_time"],"stop_loss":{"unit":"unrealized_pnl_ratio","disabled_value":0,"evaluation_price_basis":"closed_candle_mark","intrabar_stop_modeled":false,"limitation_reasons":["intra_candle_path_unavailable","candle_close_stop_may_exit_later_than_real_stop"]},"max_holding_time":{"unit":"minutes","disabled_value":0},"opposite_cross":{"min_take_profit_ratio":"max(configured, roundtrip_fee)","small_loss_tolerance_ratio":"defer_noise_band"}},"parameter_schema":[{"name":"SMA_SHORT","type":"int","required":true,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_LONG","type":"int","required":true,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_GAP_MIN_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"price_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_VOL_WINDOW","type":"int","required":false,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_VOL_MIN_RANGE_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"price_range_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_VOLUME_WINDOW","type":"int","required":false,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":false,"behavior_affecting":false,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_LIQUIDITY_WINDOW","type":"int","required":false,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":false,"behavior_affecting":false,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_OVEREXT_LOOKBACK","type":"int","required":false,"min":1,"max":null,"enum":[],"unit":"candles","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_FILTER_OVEREXT_MAX_RETURN_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"return_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_MARKET_REGIME_ENABLED","type":"bool","required":false,"min":null,"max":null,"enum":[],"unit":"enabled_flag","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_COST_EDGE_ENABLED","type":"bool","required":false,"min":null,"max":null,"enum":[],"unit":"enabled_flag","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"SMA_COST_EDGE_MIN_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"edge_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"ENTRY_EDGE_BUFFER_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"edge_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_MIN_EXPECTED_EDGE_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"edge_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_ENTRY_SLIPPAGE_BPS","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"basis_points","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"LIVE_FEE_RATE_ESTIMATE","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"fee_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_EXIT_RULES","type":"str","required":false,"min":null,"max":null,"enum":[],"unit":"comma_separated_exit_rule_names","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_EXIT_STOP_LOSS_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"unrealized_pnl_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_EXIT_MAX_HOLDING_MIN","type":"int","required":false,"min":0,"max":null,"enum":[],"unit":"minutes","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"pnl_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""},{"name":"STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO","type":"float","required":false,"min":0.0,"max":null,"enum":[],"unit":"pnl_ratio","runtime_bound":true,"behavior_affecting":true,"deprecated_keys":[],"migration_rule":""}]}'''
)

GOLDEN_MATERIALIZED_PARAMETERS = {
    **GOLDEN_SPEC_PAYLOAD["default_parameters"],
    "SMA_SHORT": 7,
    "SMA_LONG": 30,
    "LIVE_FEE_RATE_ESTIMATE": 0.0007,
    "STRATEGY_ENTRY_SLIPPAGE_BPS": 1.5,
}
GOLDEN_RUNTIME_BOUND_NAMES = (
    "SMA_SHORT", "SMA_LONG", "SMA_FILTER_GAP_MIN_RATIO", "SMA_FILTER_VOL_WINDOW",
    "SMA_FILTER_VOL_MIN_RANGE_RATIO", "SMA_FILTER_OVEREXT_LOOKBACK", "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO",
    "SMA_MARKET_REGIME_ENABLED", "SMA_COST_EDGE_ENABLED", "SMA_COST_EDGE_MIN_RATIO", "ENTRY_EDGE_BUFFER_RATIO",
    "STRATEGY_MIN_EXPECTED_EDGE_RATIO", "STRATEGY_ENTRY_SLIPPAGE_BPS", "LIVE_FEE_RATE_ESTIMATE",
    "STRATEGY_EXIT_RULES", "STRATEGY_EXIT_STOP_LOSS_RATIO", "STRATEGY_EXIT_MAX_HOLDING_MIN",
    "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO", "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO",
)


def test_sma_spec_payload_and_hash_match_fixed_research_compatible_golden_values() -> None:
    assert json.loads(json.dumps(SMA_WITH_FILTER_SPEC.as_dict())) == GOLDEN_SPEC_PAYLOAD
    assert SMA_WITH_FILTER_SPEC.as_dict()["exit_policy_schema"] == {
        "schema_version": 1,
        "rules": ("stop_loss", "opposite_cross", "max_holding_time"),
        "stop_loss": {
            "unit": "unrealized_pnl_ratio",
            "disabled_value": 0,
            "evaluation_price_basis": "closed_candle_mark",
            "intrabar_stop_modeled": False,
            "limitation_reasons": (
                "intra_candle_path_unavailable",
                "candle_close_stop_may_exit_later_than_real_stop",
            ),
        },
        "max_holding_time": {"unit": "minutes", "disabled_value": 0},
        "opposite_cross": {
            "min_take_profit_ratio": "max(configured, roundtrip_fee)",
            "small_loss_tolerance_ratio": "defer_noise_band",
        },
    }
    assert SMA_WITH_FILTER_SPEC.spec_hash() == "sha256:059104409a48f6146f852d481b333a503718525b1b6539013f42125c65907757"
    assert strategy_spec_for_name("sma_with_filter") is SMA_WITH_FILTER_SPEC
    assert strategy_spec_for_name("test_top_of_book_required") is SMA_WITH_FILTER_SPEC


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"SMA_LONG": 30}, "missing required strategy parameter(s): SMA_SHORT"),
        ({"SMA_SHORT": 7, "SMA_LONG": 30, "UNKNOWN": 1}, "unknown strategy parameter(s): UNKNOWN"),
        ({"SMA_SHORT": True, "SMA_LONG": 30}, "SMA_SHORT must be int"),
        ({"SMA_SHORT": 0, "SMA_LONG": 30}, "SMA_SHORT must be >= 1"),
    ],
)
def test_sma_parameter_validation_preserves_required_unknown_type_and_range_errors(parameters: dict[str, object], message: str) -> None:
    with pytest.raises(StrategySpecError, match=re.escape(message)):
        SMA_WITH_FILTER_SPEC.validate_parameters(parameters)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sma_parameter_validation_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(StrategySpecError, match="SMA_FILTER_GAP_MIN_RATIO must be finite"):
        materialize_strategy_parameters("sma_with_filter", {"SMA_SHORT": 7, "SMA_LONG": 30, "SMA_FILTER_GAP_MIN_RATIO": value})


def test_materialization_sources_runtime_bound_names_and_hash_match_fixed_golden_values() -> None:
    raw = {"SMA_SHORT": 7, "SMA_LONG": 30}
    materialized = materialize_strategy_parameters("sma_with_filter", raw, fee_rate=0.0007, slippage_bps=1.5)

    assert materialized == GOLDEN_MATERIALIZED_PARAMETERS
    assert materialized_strategy_parameters_hash(materialized) == "sha256:66d2a551434e5ffd8da2d50a28276bc37514a8a45c1ca3f4ee9ca1f80663ec12"
    assert strategy_parameter_source_map("sma_with_filter", raw, fee_rate=0.0007, slippage_bps=1.5) == {
        **{name: "strategy_spec_default" for name in GOLDEN_SPEC_PAYLOAD["default_parameters"]},
        "SMA_SHORT": "raw_parameter_values",
        "SMA_LONG": "raw_parameter_values",
        "LIVE_FEE_RATE_ESTIMATE": "cost_model_fee_rate",
        "STRATEGY_ENTRY_SLIPPAGE_BPS": "cost_model_slippage_bps",
    }
    assert runtime_bound_behavior_parameter_names("sma_with_filter") == GOLDEN_RUNTIME_BOUND_NAMES


def test_exit_policy_hash_matches_fixed_golden_value() -> None:
    assert exit_policy_hash({"schema_version": 1, "rules": ["stop_loss", "opposite_cross", "max_holding_time"]}) == "sha256:2d5dbb0d6ad5dc96e06a804511cbbe0ee687f695376e43da77208957ba4720da"


def test_operation_strategy_spec_does_not_import_research() -> None:
    source = ast.parse(Path(operation_spec.__file__).read_text(encoding="utf-8"))
    assert all(
        not (
            isinstance(node, ast.Import)
            and any(alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.") for alias in node.names)
        )
        and not (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "bithumb_bot.research" or node.module.startswith("bithumb_bot.research."))
        )
        for node in ast.walk(source)
    )
