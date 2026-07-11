"""Operation-owned strategy registration and legacy-plugin projection.

This module deliberately owns only runtime-facing registration state.  Plugin
discovery remains outside this boundary during the transition.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .capabilities import (
    DataCapabilityRequirement,
    OperationStrategyDataRequirements,
    RuntimeDataRequirementBuilder,
    RuntimeParameterAdapter,
    StrategyRuntimeCapabilities,
)


class OperationStrategyRegistryError(ValueError):
    """Raised when an operation strategy registration is invalid or unavailable."""


def _normalized_strategy_name(value: object) -> str:
    name = str(value or "").strip().lower()
    if not name:
        raise OperationStrategyRegistryError("operation strategy plugin name must be non-empty")
    return name


def _normalized_data_tuple(value: object, *, field_name: str) -> tuple[object, ...]:
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise OperationStrategyRegistryError(
            f"operation strategy plugin {field_name} must be iterable"
        ) from exc


@dataclass(frozen=True)
class OperationStrategyRegistration:
    """Runtime-facing projection of a strategy plugin's compatibility contract."""

    name: str
    version: str
    spec: object
    required_data: tuple[object, ...]
    optional_data: tuple[object, ...]
    runtime_replay_builder: Any | None
    runtime_parameter_adapter: RuntimeParameterAdapter | None
    runtime_decision_adapter_factory: Any | None
    runtime_feature_snapshot_builder: Any | None
    single_replay_bundle_builder: Any | None
    policy_assembly_factory: Any | None
    runtime_data_requirement_builder: RuntimeDataRequirementBuilder | None
    exit_policy_materializer: Any | None
    runtime_capabilities: StrategyRuntimeCapabilities
    decision_evidence_contract: object
    decision_contract_version: str
    diagnostics_namespace: str
    compatibility_contract_payload: Mapping[str, Any]
    compatibility_contract_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _normalized_strategy_name(self.name))
        version = str(self.version or "").strip()
        if not version:
            raise OperationStrategyRegistryError("operation strategy plugin version must be non-empty")
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "required_data",
            _normalized_data_tuple(self.required_data, field_name="required_data"),
        )
        object.__setattr__(
            self,
            "optional_data",
            _normalized_data_tuple(self.optional_data, field_name="optional_data"),
        )
        if not isinstance(self.runtime_capabilities, StrategyRuntimeCapabilities):
            raise OperationStrategyRegistryError(
                "operation strategy plugin runtime_capabilities must be StrategyRuntimeCapabilities"
            )
        if not isinstance(self.compatibility_contract_payload, Mapping):
            raise OperationStrategyRegistryError(
                "operation strategy plugin compatibility contract payload must be a mapping"
            )
        compatibility_contract_hash = str(self.compatibility_contract_hash or "").strip()
        if not compatibility_contract_hash.startswith("sha256:"):
            raise OperationStrategyRegistryError(
                "operation strategy plugin compatibility contract hash must start with sha256:"
            )
        object.__setattr__(self, "compatibility_contract_hash", compatibility_contract_hash)
        decision_contract_version = str(self.decision_contract_version or "").strip()
        if not decision_contract_version:
            raise OperationStrategyRegistryError(
                "operation strategy plugin decision contract version must be non-empty"
            )
        object.__setattr__(self, "decision_contract_version", decision_contract_version)
        object.__setattr__(
            self,
            "compatibility_contract_payload",
            deepcopy(dict(self.compatibility_contract_payload)),
        )

    def contract_payload(self) -> dict[str, Any]:
        """Return an independent copy of the legacy compatibility payload."""
        return deepcopy(dict(self.compatibility_contract_payload))

    def contract_hash(self) -> str:
        """Preserve the legacy compatibility hash without projection re-hashing."""
        return self.compatibility_contract_hash


def operation_registration_from_legacy_plugin(plugin: object) -> OperationStrategyRegistration:
    """Project a legacy plugin by duck typing; no legacy plugin type is imported here."""
    try:
        payload_builder = getattr(plugin, "contract_payload")
        hash_builder = getattr(plugin, "contract_hash")
    except AttributeError as exc:
        raise OperationStrategyRegistryError("legacy strategy plugin compatibility contract missing") from exc
    if not callable(payload_builder) or not callable(hash_builder):
        raise OperationStrategyRegistryError("legacy strategy plugin compatibility contract is not callable")
    payload = payload_builder()
    contract_hash = hash_builder()
    return OperationStrategyRegistration(
        name=getattr(plugin, "name", None),
        version=getattr(plugin, "version", None),
        spec=getattr(plugin, "spec", None),
        required_data=getattr(plugin, "required_data", None),
        optional_data=getattr(plugin, "optional_data", None),
        runtime_replay_builder=getattr(plugin, "runtime_replay_builder", None),
        runtime_parameter_adapter=getattr(plugin, "runtime_parameter_adapter", None),
        runtime_decision_adapter_factory=getattr(plugin, "runtime_decision_adapter_factory", None),
        runtime_feature_snapshot_builder=getattr(plugin, "runtime_feature_snapshot_builder", None),
        single_replay_bundle_builder=getattr(plugin, "single_replay_bundle_builder", None),
        policy_assembly_factory=getattr(plugin, "policy_assembly_factory", None),
        runtime_data_requirement_builder=getattr(plugin, "runtime_data_requirement_builder", None),
        exit_policy_materializer=getattr(plugin, "exit_policy_materializer", None),
        runtime_capabilities=getattr(plugin, "runtime_capabilities", None),
        decision_evidence_contract=getattr(plugin, "decision_evidence_contract", None),
        decision_contract_version=getattr(plugin, "decision_contract_version", None),
        diagnostics_namespace=getattr(plugin, "diagnostics_namespace", ""),
        compatibility_contract_payload=payload,
        compatibility_contract_hash=contract_hash,
    )


_OPERATION_STRATEGY_PLUGINS: dict[str, OperationStrategyRegistration] = {}
_TEST_TOP_OF_BOOK_REQUIRED_STRATEGY = "__test_top_of_book_required__"


def register_operation_strategy_plugin(
    registration: OperationStrategyRegistration,
    *,
    replace: bool = False,
) -> None:
    if not isinstance(registration, OperationStrategyRegistration):
        raise OperationStrategyRegistryError(
            f"operation strategy plugin invalid type: {type(registration).__name__}"
        )
    key = _normalized_strategy_name(registration.name)
    if key in _OPERATION_STRATEGY_PLUGINS and not replace:
        raise OperationStrategyRegistryError(f"duplicate operation strategy plugin name: {key}")
    _OPERATION_STRATEGY_PLUGINS[key] = registration


def resolve_operation_strategy_plugin(strategy_name: str) -> OperationStrategyRegistration:
    key = _normalized_strategy_name(strategy_name)
    try:
        return _OPERATION_STRATEGY_PLUGINS[key]
    except KeyError as exc:
        raise OperationStrategyRegistryError(f"unsupported operation strategy: {key}") from exc


def operation_strategy_data_requirements(
    strategy_name: str,
    *,
    runtime_strategy_spec: object | None = None,
) -> OperationStrategyDataRequirements:
    """Resolve runtime data requirements from the Operation-owned registry."""
    key = _normalized_strategy_name(strategy_name)
    if key == _TEST_TOP_OF_BOOK_REQUIRED_STRATEGY:
        return OperationStrategyDataRequirements(
            required_data=("candles", "top_of_book"),
            capabilities=(
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
            ),
        )
    registration = resolve_operation_strategy_plugin(key)
    if registration.runtime_data_requirement_builder is not None:
        return registration.runtime_data_requirement_builder(runtime_strategy_spec)
    return OperationStrategyDataRequirements(
        required_data=registration.required_data,
        optional_data=registration.optional_data,
    )


def list_operation_strategy_plugins() -> tuple[OperationStrategyRegistration, ...]:
    return tuple(_OPERATION_STRATEGY_PLUGINS[name] for name in sorted(_OPERATION_STRATEGY_PLUGINS))


def operation_strategy_runtime_capability_issues(
    strategy_name: str,
    *,
    live_dry_run: bool,
    live_real_order_armed: bool,
    approved_profile_path: str = "",
    require_promotion_runtime: bool = True,
    require_runtime_replay: bool = False,
    require_runtime_decision_adapter: bool = True,
) -> tuple[str, ...]:
    """Return fail-closed runtime capability reasons for an Operation registration."""
    key = str(strategy_name or "").strip().lower()
    try:
        registration = resolve_operation_strategy_plugin(key)
    except OperationStrategyRegistryError:
        return (f"strategy_plugin_not_registered:{key}",)

    capabilities = registration.runtime_capabilities
    issues: list[str] = []
    if require_promotion_runtime and not capabilities.promotion_runtime_decisions_supported:
        issues.append(
            f"promotion_runtime_unsupported_for_strategy:{registration.name}:{capabilities.fail_closed_reason}"
        )
    if require_runtime_replay and not capabilities.runtime_replay_supported:
        issues.append(
            f"runtime_replay_unsupported_for_strategy:{registration.name}:{capabilities.fail_closed_reason}"
        )
    if require_runtime_decision_adapter and registration.runtime_decision_adapter_factory is None:
        issues.append(
            f"runtime_decision_adapter_unsupported_for_strategy:{registration.name}:{capabilities.fail_closed_reason}"
        )
    if bool(live_dry_run) and not capabilities.live_dry_run_allowed:
        issues.append(
            f"live_dry_run_not_allowed_for_strategy:{registration.name}:{capabilities.fail_closed_reason}"
        )
    if bool(live_real_order_armed) and not capabilities.live_real_order_allowed:
        issues.append(
            f"live_real_order_not_allowed_for_strategy:{registration.name}:{capabilities.fail_closed_reason}"
        )
    if (
        (bool(live_dry_run) or bool(live_real_order_armed))
        and capabilities.approved_profile_required
        and not str(approved_profile_path or "").strip()
    ):
        issues.append(f"approved_profile_required_for_strategy:{registration.name}")
    return tuple(issues)


def clear_operation_strategy_registry_for_tests() -> None:
    _OPERATION_STRATEGY_PLUGINS.clear()


def reload_operation_strategy_plugins_for_tests() -> None:
    """Test reset alias kept symmetrical with the legacy registry reset API."""
    clear_operation_strategy_registry_for_tests()
