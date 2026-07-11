from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bithumb_bot.operation_strategy.capabilities import (
    RuntimeParameterAdapter,
    StrategyRuntimeCapabilities,
)
from bithumb_bot.operation_strategy.registry import (
    OperationStrategyRegistration,
    OperationStrategyRegistryError,
    clear_operation_strategy_registry_for_tests,
    list_operation_strategy_plugins,
    operation_registration_from_legacy_plugin,
    register_operation_strategy_plugin,
    resolve_operation_strategy_plugin,
)


def _capabilities() -> StrategyRuntimeCapabilities:
    return StrategyRuntimeCapabilities(
        promotion_runtime_decisions_supported=True,
        runtime_replay_supported=True,
        fail_closed_reason="unit_test_capability_missing",
    )


def _registration(
    name: str = "unit_strategy",
    *,
    compatibility_contract_hash: str = "sha256:unit-contract",
    payload: dict[str, object] | None = None,
) -> OperationStrategyRegistration:
    return OperationStrategyRegistration(
        name=name,
        version="unit.v1",
        spec=object(),
        required_data=("candles",),
        optional_data=("top_of_book",),
        runtime_replay_builder=None,
        runtime_parameter_adapter=None,
        runtime_decision_adapter_factory=None,
        runtime_feature_snapshot_builder=None,
        single_replay_bundle_builder=None,
        policy_assembly_factory=None,
        runtime_data_requirement_builder=None,
        exit_policy_materializer=None,
        runtime_capabilities=_capabilities(),
        decision_evidence_contract=object(),
        decision_contract_version="unit_decision.v1",
        diagnostics_namespace="unit_strategy",
        compatibility_contract_payload=payload or {"nested": {"source": "legacy"}},
        compatibility_contract_hash=compatibility_contract_hash,
    )


@pytest.fixture(autouse=True)
def _clear_operation_registry() -> None:
    clear_operation_strategy_registry_for_tests()
    yield
    clear_operation_strategy_registry_for_tests()


def test_operation_strategy_registry_registers_resolves_normalizes_and_sorts() -> None:
    beta = _registration(" Beta ")
    alpha = _registration("ALPHA")

    register_operation_strategy_plugin(beta)
    register_operation_strategy_plugin(alpha)

    assert resolve_operation_strategy_plugin(" beta ") is beta
    assert beta.name == "beta"
    assert [registration.name for registration in list_operation_strategy_plugins()] == ["alpha", "beta"]


def test_operation_strategy_registry_rejects_blank_duplicate_and_unknown_names() -> None:
    with pytest.raises(OperationStrategyRegistryError, match="name must be non-empty"):
        _registration(" ")

    registration = _registration("duplicate")
    register_operation_strategy_plugin(registration)
    with pytest.raises(OperationStrategyRegistryError, match="duplicate operation strategy plugin name"):
        register_operation_strategy_plugin(_registration("DUPLICATE"))
    with pytest.raises(OperationStrategyRegistryError, match="unsupported operation strategy"):
        resolve_operation_strategy_plugin("missing")


def test_operation_strategy_registry_allows_explicit_replace_and_reset() -> None:
    first = _registration("replaceable", compatibility_contract_hash="sha256:first")
    replacement = _registration("REPLACEABLE", compatibility_contract_hash="sha256:replacement")
    register_operation_strategy_plugin(first)
    register_operation_strategy_plugin(replacement, replace=True)

    assert resolve_operation_strategy_plugin("replaceable") is replacement
    clear_operation_strategy_registry_for_tests()
    assert list_operation_strategy_plugins() == ()


def test_operation_strategy_registration_rejects_invalid_compatibility_contract_hash() -> None:
    with pytest.raises(OperationStrategyRegistryError, match="hash must start with sha256"):
        _registration(compatibility_contract_hash="not-a-compatibility-hash")


def test_operation_strategy_registration_returns_defensive_contract_payload_copy() -> None:
    registration = _registration(payload={"nested": {"value": "original"}})

    payload = registration.contract_payload()
    payload["nested"]["value"] = "changed"  # type: ignore[index]

    assert registration.contract_payload() == {"nested": {"value": "original"}}
    assert registration.contract_hash() == "sha256:unit-contract"


def test_legacy_plugin_projection_preserves_runtime_facing_contract_references() -> None:
    spec = object()
    evidence_contract = object()

    def from_env(_env: dict[str, str]) -> dict[str, object]:
        return {}

    def from_settings(_settings: object) -> dict[str, object]:
        return {}

    parameter_adapter = RuntimeParameterAdapter(from_env=from_env, from_settings=from_settings)
    runtime_replay_builder = object()
    runtime_decision_adapter_factory = object()
    runtime_data_requirement_builder = object()
    policy_assembly_factory = object()
    legacy_plugin = SimpleNamespace(
        name=" Legacy Unit ",
        version="legacy.v1",
        spec=spec,
        required_data=["candles", "orderbook_top"],
        optional_data=["trades"],
        runtime_replay_builder=runtime_replay_builder,
        runtime_parameter_adapter=parameter_adapter,
        runtime_decision_adapter_factory=runtime_decision_adapter_factory,
        runtime_feature_snapshot_builder=object(),
        single_replay_bundle_builder=object(),
        policy_assembly_factory=policy_assembly_factory,
        runtime_data_requirement_builder=runtime_data_requirement_builder,
        exit_policy_materializer=object(),
        runtime_capabilities=_capabilities(),
        decision_evidence_contract=evidence_contract,
        decision_contract_version="legacy_decision.v1",
        diagnostics_namespace="legacy_unit",
        contract_payload=lambda: {"schema_version": 1, "nested": {"legacy": True}},
        contract_hash=lambda: "sha256:legacy-contract",
    )

    registration = operation_registration_from_legacy_plugin(legacy_plugin)

    assert registration.name == "legacy unit"
    assert registration.version == "legacy.v1"
    assert registration.spec is spec
    assert registration.required_data == ("candles", "orderbook_top")
    assert registration.optional_data == ("trades",)
    assert registration.runtime_capabilities is legacy_plugin.runtime_capabilities
    assert registration.runtime_parameter_adapter is parameter_adapter
    assert registration.runtime_decision_adapter_factory is runtime_decision_adapter_factory
    assert registration.runtime_data_requirement_builder is runtime_data_requirement_builder
    assert registration.policy_assembly_factory is policy_assembly_factory
    assert registration.decision_evidence_contract is evidence_contract
    assert registration.contract_payload() == {"schema_version": 1, "nested": {"legacy": True}}
    assert registration.contract_hash() == "sha256:legacy-contract"


def test_operation_registry_module_has_no_research_import_boundary() -> None:
    path = Path("src/bithumb_bot/operation_strategy/registry.py")
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


# Remove this transitional compatibility test when the research directory is finally removed.
def test_research_discovery_dual_registers_equivalent_operation_projections() -> None:
    from bithumb_bot.research.strategy_registry import (
        StrategyRuntimeCapabilities as ResearchStrategyRuntimeCapabilities,
        list_research_strategy_plugins,
        reload_research_strategy_plugins_for_tests,
    )

    reload_research_strategy_plugins_for_tests()
    research_plugins = {plugin.name: plugin for plugin in list_research_strategy_plugins()}
    operation_registrations = {
        registration.name: registration for registration in list_operation_strategy_plugins()
    }

    assert set(operation_registrations) == set(research_plugins)
    assert ResearchStrategyRuntimeCapabilities is StrategyRuntimeCapabilities
    for name, research_plugin in research_plugins.items():
        registration = operation_registrations[name]
        assert registration.name == research_plugin.name
        assert registration.version == research_plugin.version
        assert registration.contract_hash() == research_plugin.contract_hash()
        assert registration.required_data == research_plugin.required_data
        assert registration.optional_data == research_plugin.optional_data
        assert registration.runtime_capabilities is research_plugin.runtime_capabilities
        assert isinstance(registration.runtime_capabilities, StrategyRuntimeCapabilities)
        assert registration.runtime_replay_builder is research_plugin.runtime_replay_builder
        assert registration.runtime_parameter_adapter is research_plugin.runtime_parameter_adapter
        assert registration.runtime_decision_adapter_factory is research_plugin.runtime_decision_adapter_factory
        assert registration.runtime_data_requirement_builder is research_plugin.runtime_data_requirement_builder
        assert registration.policy_assembly_factory is research_plugin.policy_assembly_factory
