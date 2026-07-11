from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bithumb_bot import runtime_adapter_bootstrap
from bithumb_bot.operation_strategy.capabilities import RuntimeParameterAdapter
from bithumb_bot.operation_strategy.registry import (
    OperationStrategyRegistryError,
    clear_operation_strategy_registry_for_tests,
    operation_runtime_strategy_parameters_from_settings,
    register_operation_strategy_plugin,
    resolve_operation_strategy_plugin,
)
from bithumb_bot.operation_strategy.spec import (
    SMA_WITH_FILTER_SPEC,
    materialize_strategy_parameters,
    materialize_strategy_parameters_for_spec,
    materialized_strategy_parameters_hash,
    runtime_bound_behavior_parameter_names,
    runtime_bound_behavior_parameter_names_for_spec,
    strategy_parameter_source_map,
    strategy_parameter_source_map_for_spec,
)


def _sma_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "STRATEGY_PARAMETERS_JSON": "",
        "SMA_SHORT": 7,
        "SMA_LONG": 30,
        "SMA_FILTER_GAP_MIN_RATIO": 0.0012,
        "SMA_FILTER_VOL_WINDOW": 10,
        "SMA_FILTER_VOL_MIN_RANGE_RATIO": 0.003,
        "SMA_FILTER_OVEREXT_LOOKBACK": 3,
        "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO": 0.02,
        "SMA_MARKET_REGIME_ENABLED": True,
        "SMA_COST_EDGE_ENABLED": True,
        "SMA_COST_EDGE_MIN_RATIO": 0.0,
        "ENTRY_EDGE_BUFFER_RATIO": 0.0005,
        "STRATEGY_MIN_EXPECTED_EDGE_RATIO": 0.0,
        "STRATEGY_ENTRY_SLIPPAGE_BPS": 1.5,
        "LIVE_FEE_RATE_ESTIMATE": 0.0007,
        "STRATEGY_EXIT_RULES": "stop_loss,opposite_cross,max_holding_time",
        "STRATEGY_EXIT_STOP_LOSS_RATIO": 0.0,
        "STRATEGY_EXIT_MAX_HOLDING_MIN": 0,
        "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO": 0.0,
        "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO": 0.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sma_registration():
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    return resolve_operation_strategy_plugin("sma_with_filter")


def _register_sma_derived(
    name: str,
    *,
    runtime_parameter_adapter: RuntimeParameterAdapter | None,
) -> None:
    register_operation_strategy_plugin(
        replace(
            _sma_registration(),
            name=name,
            runtime_parameter_adapter=runtime_parameter_adapter,
        ),
        replace=True,
    )


@pytest.fixture(autouse=True)
def _restore_discovered_operation_registry() -> None:
    from bithumb_bot.research.strategy_registry import reload_research_strategy_plugins_for_tests

    reload_research_strategy_plugins_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()
    yield
    reload_research_strategy_plugins_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()


def test_empty_operation_registry_bootstraps_and_extracts_sma_parameters() -> None:
    from bithumb_bot.research import strategy_registry

    strategy_registry._RESEARCH_STRATEGY_PLUGINS = {}
    strategy_registry._DISCOVERED_STRATEGY_PLUGINS_LOADED = False
    clear_operation_strategy_registry_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()

    parameters = operation_runtime_strategy_parameters_from_settings(
        "sma_with_filter",
        _sma_settings(),
    )

    assert parameters["SMA_SHORT"] == 7
    assert resolve_operation_strategy_plugin("sma_with_filter").name == "sma_with_filter"


def test_repeated_bootstrap_and_parameter_extraction_are_idempotent() -> None:
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    first = operation_runtime_strategy_parameters_from_settings("sma_with_filter", _sma_settings())
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    second = operation_runtime_strategy_parameters_from_settings("sma_with_filter", _sma_settings())

    assert second == first


def test_json_fallback_wins_and_materializes_spec_defaults() -> None:
    def _raising_adapter(_cfg: object) -> dict[str, object]:
        raise RuntimeError("adapter should not run")

    _register_sma_derived(
        "json_priority",
        runtime_parameter_adapter=RuntimeParameterAdapter(
            from_env=lambda _env: {},
            from_settings=_raising_adapter,
        ),
    )

    parameters = operation_runtime_strategy_parameters_from_settings(
        "json_priority",
        _sma_settings(STRATEGY_PARAMETERS_JSON='{"SMA_SHORT": 8, "SMA_LONG": 34}'),
    )

    assert parameters == materialize_strategy_parameters_for_spec(
        _sma_registration().spec,
        {"SMA_SHORT": 8, "SMA_LONG": 34},
    )
    assert parameters["SMA_FILTER_GAP_MIN_RATIO"] == 0.0012


def test_settings_adapter_fallback_returns_accepted_runtime_parameters() -> None:
    registration = _sma_registration()
    cfg = _sma_settings()

    parameters = operation_runtime_strategy_parameters_from_settings("sma_with_filter", cfg)

    assert parameters == registration.runtime_parameter_adapter.from_settings(cfg)  # type: ignore[union-attr]
    assert set(parameters) <= set(registration.spec.accepted_parameter_names)


def test_unsupported_adapter_key_fails_closed_with_existing_error_text() -> None:
    _register_sma_derived(
        "unsupported_key",
        runtime_parameter_adapter=RuntimeParameterAdapter(
            from_env=lambda _env: {},
            from_settings=lambda _cfg: {"SMA_SHORT": 7, "SMA_LONG": 30, "UNSUPPORTED": 1},
        ),
    )

    with pytest.raises(
        OperationStrategyRegistryError,
        match="runtime parameter extraction returned unsupported keys:unsupported_key:UNSUPPORTED",
    ):
        operation_runtime_strategy_parameters_from_settings("unsupported_key", _sma_settings())


def test_missing_adapter_and_unknown_strategy_fail_closed() -> None:
    _register_sma_derived("no_adapter", runtime_parameter_adapter=None)

    with pytest.raises(
        OperationStrategyRegistryError,
        match="runtime parameter extraction unsupported: no_adapter",
    ):
        operation_runtime_strategy_parameters_from_settings("no_adapter", _sma_settings())
    with pytest.raises(
        OperationStrategyRegistryError,
        match="unsupported operation strategy: unknown_strategy",
    ):
        operation_runtime_strategy_parameters_from_settings("unknown_strategy", _sma_settings())


def test_adapter_and_materialization_errors_propagate() -> None:
    def _raising_adapter(_cfg: object) -> dict[str, object]:
        raise RuntimeError("adapter failure")

    _register_sma_derived(
        "raising_adapter",
        runtime_parameter_adapter=RuntimeParameterAdapter(
            from_env=lambda _env: {},
            from_settings=_raising_adapter,
        ),
    )

    with pytest.raises(RuntimeError, match="adapter failure"):
        operation_runtime_strategy_parameters_from_settings("raising_adapter", _sma_settings())
    with pytest.raises(ValueError, match="SMA_SHORT must be int"):
        operation_runtime_strategy_parameters_from_settings(
            "sma_with_filter",
            _sma_settings(STRATEGY_PARAMETERS_JSON='{"SMA_SHORT": true, "SMA_LONG": 30}'),
        )


def test_spec_object_helpers_preserve_sma_name_helper_payload_hash_and_sources() -> None:
    raw = {"SMA_SHORT": 7, "SMA_LONG": 30}
    materialized_from_spec = materialize_strategy_parameters_for_spec(
        SMA_WITH_FILTER_SPEC,
        raw,
        fee_rate=0.0007,
        slippage_bps=1.5,
    )
    materialized_from_name = materialize_strategy_parameters(
        "sma_with_filter",
        raw,
        fee_rate=0.0007,
        slippage_bps=1.5,
    )

    assert materialized_from_spec == materialized_from_name
    assert materialized_strategy_parameters_hash(materialized_from_spec) == materialized_strategy_parameters_hash(
        materialized_from_name
    )
    assert materialized_from_spec["LIVE_FEE_RATE_ESTIMATE"] == 0.0007
    assert materialized_from_spec["STRATEGY_ENTRY_SLIPPAGE_BPS"] == 1.5
    assert runtime_bound_behavior_parameter_names_for_spec(SMA_WITH_FILTER_SPEC) == (
        runtime_bound_behavior_parameter_names("sma_with_filter")
    )
    assert strategy_parameter_source_map_for_spec(
        SMA_WITH_FILTER_SPEC,
        raw,
        fee_rate=0.0007,
        slippage_bps=1.5,
    ) == strategy_parameter_source_map(
        "sma_with_filter",
        raw,
        fee_rate=0.0007,
        slippage_bps=1.5,
    )



@pytest.mark.parametrize(
    "relative_path",
    (
        "src/bithumb_bot/operation_strategy/registry.py",
        "src/bithumb_bot/operation_strategy/spec.py",
        "src/bithumb_bot/runtime_adapters/sma_with_filter.py",
    ),
)
def test_operation_parameter_modules_have_no_direct_research_import(relative_path: str) -> None:
    path = Path(relative_path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]

    assert not any(
        module == "bithumb_bot.research" or module.startswith("bithumb_bot.research.")
        for module in imported_modules
    )
