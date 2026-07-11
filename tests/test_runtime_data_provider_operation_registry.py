from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bithumb_bot import runtime_adapter_bootstrap
from bithumb_bot.operation_strategy import registry as operation_registry
from bithumb_bot.operation_strategy.capabilities import (
    DataCapabilityRequirement,
    OperationStrategyDataRequirements,
)
from bithumb_bot.operation_strategy.registry import (
    OperationStrategyRegistryError,
    clear_operation_strategy_registry_for_tests,
    list_operation_strategy_plugins,
    operation_strategy_data_requirements,
    register_operation_strategy_plugin,
    resolve_operation_strategy_plugin,
)
from bithumb_bot.runtime_data_provider import RuntimeDataRequirementResolver
from bithumb_bot.runtime_strategy_set import RuntimeStrategySpec


def _spec(name: str, instance_id: str, *, parameters: dict[str, object] | None = None) -> RuntimeStrategySpec:
    return RuntimeStrategySpec(
        name,
        strategy_instance_id=instance_id,
        pair="KRW-BTC",
        interval="1m",
        parameters=parameters or {},
    )


def _strategy_set(*specs: RuntimeStrategySpec) -> SimpleNamespace:
    return SimpleNamespace(active_strategies=specs)


@pytest.fixture(autouse=True)
def _restore_discovered_operation_registry() -> None:
    yield
    from bithumb_bot.research.strategy_registry import reload_research_strategy_plugins_for_tests

    reload_research_strategy_plugins_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()


def _register_test_registration(
    name: str,
    *,
    required_data: tuple[str, ...] = ("candles",),
    optional_data: tuple[str, ...] = (),
    builder: object | None = None,
) -> None:
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    template = resolve_operation_strategy_plugin("canary_non_sma")
    register_operation_strategy_plugin(
        replace(
            template,
            name=name,
            required_data=required_data,
            optional_data=optional_data,
            runtime_data_requirement_builder=builder,
        ),
        replace=True,
    )


def test_empty_operation_registry_is_populated_before_data_requirement_resolution() -> None:
    from bithumb_bot.research import strategy_registry

    strategy_registry._RESEARCH_STRATEGY_PLUGINS = {}
    strategy_registry._DISCOVERED_STRATEGY_PLUGINS_LOADED = False
    clear_operation_strategy_registry_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()

    requirements = RuntimeDataRequirementResolver().resolve_for_strategy_set(
        _strategy_set(_spec("canary_non_sma", "canary"))
    )

    assert requirements.required_names == ("candles",)
    assert list_operation_strategy_plugins()


def test_repeated_bootstrap_and_requirement_resolution_are_idempotent() -> None:
    strategy_set = _strategy_set(_spec("canary_non_sma", "canary"))
    resolver = RuntimeDataRequirementResolver()

    first = resolver.resolve_for_strategy_set(strategy_set)
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    second = resolver.resolve_for_strategy_set(strategy_set)

    assert first.as_dict() == second.as_dict()
    assert first.content_hash() == second.content_hash()


def test_operation_resolver_builder_wins_and_receives_the_identical_runtime_spec() -> None:
    seen: list[object | None] = []
    expected = OperationStrategyDataRequirements(required_data=("trades",))

    def builder(runtime_strategy_spec: object | None) -> OperationStrategyDataRequirements:
        seen.append(runtime_strategy_spec)
        return expected

    _register_test_registration(
        "builder_priority",
        required_data=("candles",),
        optional_data=("top_of_book",),
        builder=builder,
    )
    spec = _spec("builder_priority", "builder")

    assert operation_strategy_data_requirements(" Builder_Priority ", runtime_strategy_spec=spec) is expected
    assert seen == [spec]


def test_operation_resolver_uses_registration_data_when_builder_is_absent() -> None:
    _register_test_registration(
        "fallback_requirements",
        required_data=("candles", "top_of_book"),
        optional_data=("trades",),
    )

    requirements = operation_strategy_data_requirements("fallback_requirements")

    assert requirements.required_data == ("candles", "top_of_book")
    assert requirements.optional_data == ("trades",)


def test_operation_resolver_fails_closed_for_unknown_strategy_and_propagates_builder_error() -> None:
    with pytest.raises(OperationStrategyRegistryError, match="unsupported operation strategy: unknown_strategy"):
        operation_strategy_data_requirements(" Unknown_Strategy ")

    def raising_builder(_runtime_strategy_spec: object | None) -> OperationStrategyDataRequirements:
        raise RuntimeError("builder failure")

    _register_test_registration("raising_builder", builder=raising_builder)
    with pytest.raises(RuntimeError, match="builder failure"):
        operation_strategy_data_requirements("raising_builder")


def test_private_top_of_book_required_hook_contract_is_preserved() -> None:
    requirements = operation_strategy_data_requirements(" __TEST_TOP_OF_BOOK_REQUIRED__ ")

    assert requirements.required_data == ("candles", "top_of_book")
    assert requirements.optional_data == ()
    assert requirements.unsupported_without == ()
    assert requirements.normalized_capabilities() == (
        DataCapabilityRequirement(name="candles", required=True),
        DataCapabilityRequirement(
            name="orderbook_top",
            required=True,
            min_coverage_pct=100.0,
            evidence_level="best_bid_ask",
            source="sqlite_orderbook_top_snapshots",
            notes="private test hook for required top-of-book preflight",
            max_age_ms=120_000,
            freshness_policy="max_age",
        ),
    )


def test_requirement_aggregation_preserves_normalization_priority_payload_and_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = OperationStrategyDataRequirements(
        required_data=("candles", "top_of_book"),
        optional_data=("trades", "funding"),
        capabilities=(
            DataCapabilityRequirement(name="top_of_book", required=True, min_coverage_pct=100.0),
        ),
    )
    second = OperationStrategyDataRequirements(
        required_data=("trade_ticks", "unsupported_required_capability"),
        optional_data=("candles", "open_interest"),
    )
    by_name = {"first": first, "second": second}
    monkeypatch.setattr(
        operation_registry,
        "operation_strategy_data_requirements",
        lambda strategy_name, **_kwargs: by_name[strategy_name],
    )
    resolver = RuntimeDataRequirementResolver()
    first_spec = _spec("first", "first-instance")
    second_spec = _spec("second", "second-instance")

    requirements = resolver.resolve_for_strategy_set(_strategy_set(first_spec, second_spec))
    normalized_first = resolver._normalize_research_requirements(first, spec=first_spec, strategy_name="first")
    normalized_second = resolver._normalize_research_requirements(second, spec=second_spec, strategy_name="second")

    assert requirements.required_names == (
        "candles",
        "orderbook_top",
        "trades",
        "unsupported_required_capability",
    )
    assert requirements.optional_names == ("funding", "open_interest")
    assert requirements.unsupported_required == ("unsupported_required_capability",)
    assert requirements.as_dict()["per_strategy"] == {
        "first-instance": {
            "strategy_name": "first",
            "required": ["candles", "orderbook_top"],
            "optional": ["funding", "trades"],
            "requirements_hash": normalized_first.content_hash(),
        },
        "second-instance": {
            "strategy_name": "second",
            "required": ["trades", "unsupported_required_capability"],
            "optional": ["candles", "open_interest"],
            "requirements_hash": normalized_second.content_hash(),
        },
    }
    assert requirements.content_hash() == RuntimeDataRequirementResolver().resolve_for_strategy_set(
        _strategy_set(first_spec, second_spec)
    ).content_hash()


# Remove this transitional parity test when the research directory is finally removed.
@pytest.mark.parametrize(
    ("strategy_name", "parameters"),
    (
        ("sma_with_filter", {"SMA_LONG": 8, "SMA_FILTER_VOL_WINDOW": 5}),
        ("daily_participation_sma", {"SMA_LONG": 8, "SMA_FILTER_VOL_WINDOW": 5}),
        ("canary_non_sma", {}),
        ("safe_hold", {}),
        ("__test_top_of_book_required__", {}),
    ),
)
def test_operation_data_requirement_resolver_matches_research_during_transition(
    strategy_name: str,
    parameters: dict[str, object],
) -> None:
    from bithumb_bot.research.strategy_registry import research_strategy_data_requirements

    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    spec = _spec(strategy_name, "parity", parameters=parameters)
    research = research_strategy_data_requirements(strategy_name, runtime_strategy_spec=spec)
    operation = operation_strategy_data_requirements(strategy_name, runtime_strategy_spec=spec)

    assert operation.required_data == research.required_data
    assert operation.optional_data == research.optional_data
    assert operation.unsupported_without == research.unsupported_without
    assert operation.normalized_capabilities() == research.normalized_capabilities()
    assert operation.capability_contract_payload() == research.capability_contract_payload()


def test_runtime_data_provider_has_no_direct_research_import() -> None:
    path = Path("src/bithumb_bot/runtime_data_provider.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and "research" in node.module
    ]

    assert imports == []
