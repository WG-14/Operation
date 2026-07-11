from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bithumb_bot import runtime_adapter_bootstrap, runtime_strategy_decision, runtime_strategy_set
from bithumb_bot.config import settings
from bithumb_bot.operation_strategy import registry as operation_registry
from bithumb_bot.operation_strategy.capabilities import StrategyRuntimeCapabilities
from bithumb_bot.operation_strategy.registry import (
    OperationStrategyRegistryError,
    clear_operation_strategy_registry_for_tests,
    list_operation_strategy_plugins,
    operation_strategy_runtime_capability_issues,
    resolve_operation_strategy_plugin,
)
from bithumb_bot.runtime_strategy_decision import (
    DecisionRunner,
    get_runtime_decision_adapter,
    list_runtime_decision_adapters,
)
from bithumb_bot.runtime_strategy_set import RuntimeStrategySpec


@pytest.fixture(autouse=True)
def _clear_runtime_adapter_cache() -> None:
    runtime_strategy_decision._DERIVED_RUNTIME_DECISION_ADAPTER_CACHE.clear()
    yield
    runtime_strategy_decision._DERIVED_RUNTIME_DECISION_ADAPTER_CACHE.clear()


def _registered_canary():
    list_runtime_decision_adapters()
    return resolve_operation_strategy_plugin("canary_non_sma")


def test_empty_operation_registry_is_populated_by_runtime_adapter_bootstrap() -> None:
    clear_operation_strategy_registry_for_tests()
    runtime_adapter_bootstrap.reset_runtime_decision_adapter_bootstrap_for_tests()

    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    assert list_operation_strategy_plugins()
    adapter_names = list_runtime_decision_adapters()

    assert adapter_names
    assert list_operation_strategy_plugins()


def test_repeated_bootstrap_and_adapter_listing_are_idempotent() -> None:
    first = list_runtime_decision_adapters()
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()
    runtime_adapter_bootstrap.ensure_runtime_decision_adapters_registered()

    assert list_runtime_decision_adapters() == first
    assert list_runtime_decision_adapters() == first


def test_operation_registry_resolves_normal_runtime_adapter_and_unknown_is_none() -> None:
    adapter = get_runtime_decision_adapter("sma_with_filter")

    assert adapter is not None
    assert adapter.strategy_name == "sma_with_filter"
    assert get_runtime_decision_adapter("unknown_runtime_strategy") is None


def test_promotion_runtime_unsupported_and_missing_factory_return_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _registered_canary()
    unsupported = replace(
        registration,
        runtime_capabilities=StrategyRuntimeCapabilities(
            promotion_runtime_decisions_supported=False,
            runtime_replay_supported=False,
            fail_closed_reason="unit_unsupported",
        ),
    )
    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: unsupported,
    )
    assert get_runtime_decision_adapter("canary_non_sma") is None

    no_factory = replace(registration, runtime_decision_adapter_factory=None)
    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: no_factory,
    )
    assert get_runtime_decision_adapter("canary_non_sma") is None


def test_adapter_name_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    registration = _registered_canary()

    class _WrongNameAdapter:
        strategy_name = "wrong_name"

        def decide_feature_snapshot(self, request, feature_snapshot):  # noqa: ANN001
            return None

        def typed_authority_required(self) -> bool:
            return True

    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: replace(registration, runtime_decision_adapter_factory=_WrongNameAdapter),
    )

    with pytest.raises(RuntimeError, match="runtime_decision_adapter_name_mismatch:canary_non_sma:wrong_name"):
        get_runtime_decision_adapter("canary_non_sma")


def test_feature_snapshot_and_db_bound_adapters_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    registration = _registered_canary()

    class _MissingFeatureSnapshotAdapter:
        strategy_name = "canary_non_sma"

        def typed_authority_required(self) -> bool:
            return True

    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: replace(
            registration,
            runtime_decision_adapter_factory=_MissingFeatureSnapshotAdapter,
        ),
    )
    with pytest.raises(RuntimeError, match="runtime_decision_feature_snapshot_required:canary_non_sma"):
        get_runtime_decision_adapter("canary_non_sma")

    class _DbBoundAdapter:
        strategy_name = "canary_non_sma"

        def decide_feature_snapshot(self, request, feature_snapshot):  # noqa: ANN001
            return None

        def decide(self, conn, request):  # noqa: ANN001
            return None

        def typed_authority_required(self) -> bool:
            return True

    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: replace(registration, runtime_decision_adapter_factory=_DbBoundAdapter),
    )
    with pytest.raises(RuntimeError, match="promotion_runtime_adapter_db_bound_decide_forbidden:canary_non_sma"):
        get_runtime_decision_adapter("canary_non_sma")


def test_contract_hash_keeps_single_adapter_cache_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    registration = _registered_canary()
    factories_called = 0

    class _Adapter:
        strategy_name = "canary_non_sma"

        def decide_feature_snapshot(self, request, feature_snapshot):  # noqa: ANN001
            return None

        def typed_authority_required(self) -> bool:
            return True

    def _factory() -> _Adapter:
        nonlocal factories_called
        factories_called += 1
        return _Adapter()

    first = replace(
        registration,
        compatibility_contract_hash="sha256:operation-first",
        runtime_decision_adapter_factory=_factory,
    )
    second = replace(first, compatibility_contract_hash="sha256:operation-second")
    current = first

    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: current,
    )

    first_adapter = get_runtime_decision_adapter("canary_non_sma")
    assert get_runtime_decision_adapter("canary_non_sma") is first_adapter
    current = second
    second_adapter = get_runtime_decision_adapter("canary_non_sma")

    assert second_adapter is not first_adapter
    assert factories_called == 2
    assert tuple(runtime_strategy_decision._DERIVED_RUNTIME_DECISION_ADAPTER_CACHE) == (
        ("canary_non_sma", "sha256:operation-second", "plugin"),
    )


def test_operation_capability_reasons_match_existing_reason_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _registered_canary()
    restricted = replace(
        registration,
        runtime_decision_adapter_factory=None,
        runtime_capabilities=StrategyRuntimeCapabilities(
            promotion_runtime_decisions_supported=False,
            runtime_replay_supported=False,
            fail_closed_reason="unit_restricted",
        ),
    )
    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: restricted,
    )

    assert operation_strategy_runtime_capability_issues(
        "unit_restricted",
        live_dry_run=True,
        live_real_order_armed=True,
        approved_profile_path="",
        require_promotion_runtime=True,
        require_runtime_replay=True,
        require_runtime_decision_adapter=True,
    ) == (
        "promotion_runtime_unsupported_for_strategy:canary_non_sma:unit_restricted",
        "runtime_replay_unsupported_for_strategy:canary_non_sma:unit_restricted",
        "runtime_decision_adapter_unsupported_for_strategy:canary_non_sma:unit_restricted",
        "live_dry_run_not_allowed_for_strategy:canary_non_sma:unit_restricted",
        "live_real_order_not_allowed_for_strategy:canary_non_sma:unit_restricted",
        "approved_profile_required_for_strategy:canary_non_sma",
    )

    monkeypatch.setattr(
        operation_registry,
        "resolve_operation_strategy_plugin",
        lambda _name: (_ for _ in ()).throw(OperationStrategyRegistryError("missing")),
    )
    assert operation_strategy_runtime_capability_issues(
        " Missing ",
        live_dry_run=False,
        live_real_order_armed=False,
    ) == ("strategy_plugin_not_registered:missing",)


# Remove this transitional parity test when the research directory is finally removed.
@pytest.mark.parametrize(
    ("strategy_name", "live_dry_run", "live_real_order_armed", "approved_profile_path"),
    (
        ("canary_non_sma", False, False, ""),
        ("safe_hold", True, False, "/runtime/profile.json"),
        ("sma_cross", True, True, ""),
        ("unknown_runtime_strategy", True, False, ""),
    ),
)
def test_operation_capability_issues_match_research_during_transition(
    strategy_name: str,
    live_dry_run: bool,
    live_real_order_armed: bool,
    approved_profile_path: str,
) -> None:
    from bithumb_bot.research.strategy_registry import strategy_runtime_capability_issues

    kwargs = {
        "live_dry_run": live_dry_run,
        "live_real_order_armed": live_real_order_armed,
        "approved_profile_path": approved_profile_path,
        "require_promotion_runtime": True,
        "require_runtime_replay": True,
        "require_runtime_decision_adapter": True,
    }

    assert operation_strategy_runtime_capability_issues(strategy_name, **kwargs) == strategy_runtime_capability_issues(
        strategy_name,
        **kwargs,
    )


def test_decision_runner_multi_strategy_keeps_capability_reason_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = RuntimeStrategySpec("canary_non_sma")
    monkeypatch.setattr(
        runtime_strategy_set.RuntimeStrategySetResolver,
        "resolve",
        lambda _self: SimpleNamespace(multi_strategy_enabled=True),
    )
    monkeypatch.setattr(
        runtime_strategy_set.ProfileAuthorityContext,
        "for_strategy_set",
        staticmethod(lambda _strategy_set: None),
    )
    monkeypatch.setattr(
        operation_registry,
        "operation_strategy_runtime_capability_issues",
        lambda *_args, **_kwargs: ("runtime_replay_unsupported_for_strategy:canary_non_sma:unit",),
    )
    original_mode = settings.MODE
    try:
        object.__setattr__(settings, "MODE", "live")
        with pytest.raises(
            RuntimeError,
            match=(
                "live_runtime_strategy_capability_validation_failed:canary_non_sma:"
                "reasons=runtime_replay_unsupported_for_strategy:canary_non_sma:unit"
            ),
        ):
            DecisionRunner().decide_snapshot(None, runtime_strategy_spec=spec)
    finally:
        object.__setattr__(settings, "MODE", original_mode)


def test_runtime_strategy_decision_has_no_direct_research_registry_import() -> None:
    path = Path("src/bithumb_bot/runtime_strategy_decision.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not any(
        (
            isinstance(node, ast.Import)
            and any(
                alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.")
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "research" or node.module.startswith("research."))
        )
        for node in ast.walk(tree)
    )
