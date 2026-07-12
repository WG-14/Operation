from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .decision_equivalence import sha256_prefixed


@dataclass(frozen=True)
class StrategyDecisionEvidence:
    policy_hash: str
    policy_contract_hash: str
    policy_input_hash: str
    policy_decision_hash: str
    replay_fingerprint: Mapping[str, object]
    replay_fingerprint_hash: str
    strategy_evaluation_provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "replay_fingerprint", MappingProxyType(dict(self.replay_fingerprint)))
        object.__setattr__(
            self,
            "strategy_evaluation_provenance",
            MappingProxyType(dict(self.strategy_evaluation_provenance)),
        )

    def policy_hashes(self) -> dict[str, str]:
        return {
            "pure_policy_hash": self.policy_hash,
            "policy_contract_hash": self.policy_contract_hash,
            "policy_input_hash": self.policy_input_hash,
            "policy_decision_hash": self.policy_decision_hash,
        }


class StrategyDecisionEvidenceBuilder:
    """Central builder for deterministic policy, replay, and provenance hashes."""

    def build(
        self,
        *,
        strategy_name: str,
        policy_contract_material: Mapping[str, object],
        policy_input_material: Mapping[str, object],
        policy_decision_material: Mapping[str, object],
        replay_fingerprint_material: Mapping[str, object] | None = None,
        strategy_instance_id: str | None = None,
        strategy_parameters_hash: str | None = None,
        approved_profile_hash: str | None = None,
        runtime_contract_hash: str | None = None,
        plugin_contract_hash: str | None = None,
        runtime_decision_request_hash: str | None = None,
        mode: str | None = None,
        extra_provenance: Mapping[str, object] | None = None,
    ) -> StrategyDecisionEvidence:
        name = str(strategy_name or "").strip().lower()
        if not name:
            raise ValueError("strategy_evidence_strategy_name_missing")
        policy_contract = dict(policy_contract_material)
        policy_input = dict(policy_input_material)
        policy_decision = dict(policy_decision_material)
        policy_contract_hash = sha256_prefixed(policy_contract)
        policy_input_hash = sha256_prefixed(policy_input)
        policy_decision_hash = sha256_prefixed(policy_decision)
        policy_hash = sha256_prefixed(
            {
                "policy_contract": policy_contract,
                "policy_input": policy_input,
                "policy_decision": policy_decision,
            }
        )
        replay_fingerprint = {
            "schema_version": 1,
            "strategy_name": name,
            "policy_hash": policy_hash,
            "policy_contract_hash": policy_contract_hash,
            "policy_input_hash": policy_input_hash,
            "policy_decision_hash": policy_decision_hash,
            **dict(replay_fingerprint_material or {}),
        }
        optional_bindings = {
            "strategy_instance_id": strategy_instance_id,
            "strategy_parameters_hash": strategy_parameters_hash,
            "approved_profile_hash": approved_profile_hash,
            "runtime_contract_hash": runtime_contract_hash,
            "plugin_contract_hash": plugin_contract_hash,
            "runtime_decision_request_hash": runtime_decision_request_hash,
        }
        for key, value in optional_bindings.items():
            if str(value or "").strip():
                replay_fingerprint[key] = str(value)
        replay_fingerprint_hash = sha256_prefixed(replay_fingerprint)
        provenance = {
            "strategy_name": name,
            "strategy_instance_id": strategy_instance_id,
            "strategy_parameters_hash": strategy_parameters_hash,
            "approved_profile_hash": approved_profile_hash,
            "runtime_contract_hash": runtime_contract_hash,
            "plugin_contract_hash": plugin_contract_hash,
            "runtime_decision_request_hash": runtime_decision_request_hash,
            "strategy_evaluation_mode": mode,
            "policy_hash": policy_hash,
            "policy_contract_hash": policy_contract_hash,
            "policy_input_hash": policy_input_hash,
            "policy_decision_hash": policy_decision_hash,
            "replay_fingerprint_hash": replay_fingerprint_hash,
            "replay_fingerprint": dict(replay_fingerprint),
        }
        provenance.update(dict(extra_provenance or {}))
        return StrategyDecisionEvidence(
            policy_hash=policy_hash,
            policy_contract_hash=policy_contract_hash,
            policy_input_hash=policy_input_hash,
            policy_decision_hash=policy_decision_hash,
            replay_fingerprint=replay_fingerprint,
            replay_fingerprint_hash=replay_fingerprint_hash,
            strategy_evaluation_provenance=provenance,
        )


ReplayFingerprintBuilder = StrategyDecisionEvidenceBuilder
PolicyDecisionEvidenceBuilder = StrategyDecisionEvidenceBuilder
