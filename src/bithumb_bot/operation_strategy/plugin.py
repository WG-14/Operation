"""Operation-owned runtime strategy plugin contract.

This contract intentionally has no research runner, dataset, manifest, or
export surface.  It is the only plugin shape admitted to the runtime registry.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from bithumb_bot.artifact_hashing import sha256_prefixed
from bithumb_bot.strategy_evidence_contract import DecisionEvidenceContract

from .capabilities import RuntimeDataRequirementBuilder, RuntimeParameterAdapter, StrategyRuntimeCapabilities


@dataclass(frozen=True)
class OperationStrategyPlugin:
    name: str
    version: str
    spec: object
    runtime_capabilities: StrategyRuntimeCapabilities
    runtime_parameter_adapter: RuntimeParameterAdapter | None
    runtime_decision_adapter_factory: Any | None
    runtime_feature_snapshot_builder: Any | None
    runtime_data_requirement_builder: RuntimeDataRequirementBuilder | None
    runtime_replay_builder: Any | None
    policy_assembly_factory: Any | None
    exit_policy_materializer: Any | None
    decision_evidence_contract: DecisionEvidenceContract
    required_data: tuple[str, ...] = ()
    optional_data: tuple[str, ...] = ()
    single_replay_bundle_builder: Any | None = None
    decision_contract_version: str = ""
    diagnostics_namespace: str = ""

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().lower()
        if not name:
            raise ValueError("operation_strategy_plugin_name_missing")
        if not str(self.version or "").strip():
            raise ValueError("operation_strategy_plugin_version_missing")
        if not isinstance(self.runtime_capabilities, StrategyRuntimeCapabilities):
            raise TypeError("operation_strategy_plugin_capabilities_invalid")
        if not isinstance(self.decision_evidence_contract, DecisionEvidenceContract):
            raise TypeError("operation_strategy_plugin_evidence_contract_invalid")
        if self.runtime_capabilities.promotion_runtime_decisions_supported and (
            self.runtime_decision_adapter_factory is None or self.policy_assembly_factory is None
        ):
            raise ValueError("operation_strategy_plugin_runtime_decision_contract_missing")
        if self.runtime_capabilities.runtime_replay_supported != (self.runtime_replay_builder is not None):
            raise ValueError("operation_strategy_plugin_runtime_replay_capability_mismatch")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "required_data", tuple(str(x) for x in self.required_data))
        object.__setattr__(self, "optional_data", tuple(str(x) for x in self.optional_data))
        object.__setattr__(self, "decision_contract_version", str(self.decision_contract_version or getattr(self.spec, "decision_contract_version", "")))

    def contract_payload(self) -> dict[str, Any]:
        adapter = self.runtime_parameter_adapter
        return {
            "schema_version": 1,
            "name": self.name,
            "strategy_name": self.name,
            "version": self.version,
            "strategy_spec_hash": self.spec.spec_hash(),
            "required_data": list(self.required_data),
            "optional_data": list(self.optional_data),
            "runtime_replay_supported": self.runtime_replay_builder is not None,
            "runtime_decision_supported": self.runtime_decision_adapter_factory is not None,
            "runtime_feature_snapshot_builder_supported": self.runtime_feature_snapshot_builder is not None,
            "runtime_data_requirement_builder_supported": self.runtime_data_requirement_builder is not None,
            "policy_assembly_supported": self.policy_assembly_factory is not None,
            "exit_policy_materializer_supported": self.exit_policy_materializer is not None,
            "runtime_parameter_adapter_supported": adapter is not None,
            "runtime_parameter_authority_scope": "paper_legacy_compat_only",
            "runtime_parameter_env_keys": list(adapter.env_keys) if adapter else [],
            "decision_contract_version": self.decision_contract_version,
            "diagnostics_namespace": self.diagnostics_namespace,
            "runtime_capabilities": self.runtime_capabilities.as_dict(),
            "live_eligibility": {
                "dry_run_allowed": self.runtime_capabilities.live_dry_run_allowed,
                "real_order_allowed": self.runtime_capabilities.live_real_order_allowed,
                "approved_profile_required": self.runtime_capabilities.approved_profile_required,
                "fail_closed_reason": self.runtime_capabilities.fail_closed_reason,
            },
            "decision_evidence_contract": self.decision_evidence_contract.as_dict(),
            "research_contract_included": False,
        }

    def contract_hash(self) -> str:
        return sha256_prefixed(self.contract_payload())


@dataclass(frozen=True)
class ExitPolicyMaterialization:
    exit_policy: dict[str, Any]
    exit_policy_hash: str
    exit_policy_contract_hash: str
    exit_policy_config: dict[str, Any]
    exit_policy_config_hash: str
    exit_policy_source: str
    exit_policy_materialization_mode: str

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)
