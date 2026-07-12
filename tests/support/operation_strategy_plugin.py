"""Test-only Operation strategy registrations for registry boundary coverage.

Nothing in this module is imported by production discovery.  The canary name
exists only so multi-strategy tests can exercise the registry without putting a
Research-era strategy back into the production built-in registry.
"""
from __future__ import annotations

from dataclasses import replace

from operation.operation_strategy.builtin import BUILTIN_OPERATION_STRATEGY_PLUGINS
from operation.operation_strategy.capabilities import RuntimeParameterAdapter
from operation.operation_strategy.registry import (
    OperationStrategyRegistryError,
    register_operation_strategy_plugin,
)
from operation.operation_strategy.spec import StrategyParameterSchema, StrategySpec
from operation.runtime_adapters.sma_with_filter import SmaWithFilterRuntimeDecisionAdapter


TEST_ONLY_OPERATION_STRATEGY_NAME = "canary_non_sma"

_TEST_ONLY_CANARY_PARAMETERS = {
    "CANARY_ORDER_START_INDEX": 0,
    "CANARY_ORDER_SIDE": "BUY",
    "CANARY_ORDER_REASON": "test_only_operation_plugin",
}

_TEST_ONLY_CANARY_SPEC = StrategySpec(
    strategy_name=TEST_ONLY_OPERATION_STRATEGY_NAME,
    strategy_version="test-only.operation-plugin.v1",
    accepted_parameter_names=tuple(_TEST_ONLY_CANARY_PARAMETERS),
    required_parameter_names=tuple(_TEST_ONLY_CANARY_PARAMETERS),
    behavior_affecting_parameter_names=tuple(_TEST_ONLY_CANARY_PARAMETERS),
    metadata_only_parameter_names=(),
    research_only_parameter_names=(),
    default_parameters=dict(_TEST_ONLY_CANARY_PARAMETERS),
    decision_contract_version="test_only_operation_decision_contract.v1",
    required_data=("candles",),
    optional_data=("top_of_book",),
    exit_policy_schema={"schema_version": 1, "rules": ()},
    parameter_schema=(
        StrategyParameterSchema("CANARY_ORDER_START_INDEX", "int", required=True, min_value=0),
        StrategyParameterSchema("CANARY_ORDER_SIDE", "str", required=True, enum=("BUY", "SELL", "HOLD")),
        StrategyParameterSchema("CANARY_ORDER_REASON", "str", required=True),
    ),
)


def _test_only_parameters(_source: object) -> dict[str, object]:
    return dict(_TEST_ONLY_CANARY_PARAMETERS)


class _TestOnlyOperationRuntimeAdapter(SmaWithFilterRuntimeDecisionAdapter):
    strategy_name = TEST_ONLY_OPERATION_STRATEGY_NAME


def register_test_only_operation_strategy_plugin() -> None:
    """Register the test-only multi-strategy double once per pytest session."""
    source = next(plugin for plugin in BUILTIN_OPERATION_STRATEGY_PLUGINS if plugin.name == "sma_with_filter")
    try:
        register_operation_strategy_plugin(
            replace(
                source,
                name=TEST_ONLY_OPERATION_STRATEGY_NAME,
                version="test-only.operation-plugin.v1",
                spec=_TEST_ONLY_CANARY_SPEC,
                runtime_parameter_adapter=RuntimeParameterAdapter(
                    _test_only_parameters,
                    _test_only_parameters,
                    env_keys=tuple(_TEST_ONLY_CANARY_PARAMETERS),
                ),
                required_data=(),
                optional_data=(),
                runtime_feature_snapshot_builder=None,
                runtime_data_requirement_builder=None,
                runtime_decision_adapter_factory=_TestOnlyOperationRuntimeAdapter,
                exit_policy_materializer=None,
                diagnostics_namespace="test_only_operation_plugin",
            )
        )
    except OperationStrategyRegistryError as exc:
        if f"duplicate operation strategy plugin name: {TEST_ONLY_OPERATION_STRATEGY_NAME}" not in str(exc):
            raise
