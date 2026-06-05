from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .canonical_decision import sha256_prefixed
from .risk_decision import (
    RISK_BUDGET_LEGACY_MARKER,
    RISK_BUDGET_SEMANTICS,
    build_risk_decision_artifact,
)


def build_portfolio_risk_decision(target_payload: Mapping[str, object]) -> dict[str, object]:
    status = "ALLOW" if bool(target_payload.get("authoritative")) else "BLOCK"
    reason_code = (
        "OK"
        if status == "ALLOW"
        else str(target_payload.get("fail_closed_reason") or "PORTFOLIO_TARGET_NOT_AUTHORITATIVE")
    )
    evidence = {
        "scope": "portfolio_allocation_single_pair",
        "state_source": "portfolio_allocator_target",
        "portfolio_target_hash": str(target_payload.get("final_portfolio_target_hash") or ""),
        "allocation_input_hash": str(target_payload.get("allocation_input_hash") or ""),
        "allocator_config_hash": str(target_payload.get("allocator_config_hash") or ""),
        "strategy_contribution_hash": str(target_payload.get("strategy_contribution_hash") or ""),
        "pair": str(target_payload.get("pair") or ""),
        "authoritative": bool(target_payload.get("authoritative")),
        "fail_closed_reason": str(target_payload.get("fail_closed_reason") or "none"),
        "conflict_resolution": dict(target_payload.get("conflict_resolution") or {}),
    }
    policy = {
        "schema_version": 1,
        "policy_name": "portfolio_allocation_authority_v1",
        "single_pair_runtime_required": True,
        "requires_authoritative_portfolio_target": True,
        "requires_strategy_contribution_hash": True,
        "requires_allocation_input_hash": True,
    }
    risk_input = {
        "schema_version": 1,
        "pair": str(target_payload.get("pair") or ""),
        "target_exposure_krw": target_payload.get("target_exposure_krw"),
        "target_qty": target_payload.get("target_qty"),
        "authoritative": bool(target_payload.get("authoritative")),
        "fail_closed_reason": str(target_payload.get("fail_closed_reason") or "none"),
        "allocation_input_hash": str(target_payload.get("allocation_input_hash") or ""),
        "allocator_config_hash": str(target_payload.get("allocator_config_hash") or ""),
        "strategy_contribution_hash": str(target_payload.get("strategy_contribution_hash") or ""),
        "final_portfolio_target_hash": str(target_payload.get("final_portfolio_target_hash") or ""),
    }
    policy_hash = sha256_prefixed(policy)
    input_hash = sha256_prefixed(risk_input)
    evidence_hash = sha256_prefixed(evidence)
    decision_without_hash = {
        "schema_version": 1,
        "evaluation_point": "portfolio_allocation",
        "status": status,
        "reason_code": reason_code,
        "reason": "ok" if status == "ALLOW" else f"portfolio allocation blocked: {reason_code}",
        "portfolio_risk_policy_hash": policy_hash,
        "portfolio_risk_input_hash": input_hash,
        "portfolio_risk_evidence_hash": evidence_hash,
        "state_source": "portfolio_allocator_target",
        "effective_limits": policy,
        "evidence": evidence,
    }
    decision_hash = sha256_prefixed(decision_without_hash)
    return {
        **decision_without_hash,
        "portfolio_risk_decision_hash": decision_hash,
        "risk_decision_hash": decision_hash,
        "risk_policy_hash": policy_hash,
        "risk_input_hash": input_hash,
    }


@dataclass(frozen=True)
class PortfolioTarget:
    pair: str
    target_exposure_krw: float | None
    target_qty: float | None
    allocator_policy_name: str
    allocator_policy_version: str
    allocator_config_hash: str
    strategy_contribution_hash: str
    allocation_input_hash: str
    reason: str
    conflict_resolution: Mapping[str, object] = field(default_factory=dict)
    authoritative: bool = True
    fail_closed_reason: str = "none"
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_exposure_krw",
            None if self.target_exposure_krw is None else float(self.target_exposure_krw),
        )
        object.__setattr__(
            self,
            "target_qty",
            None if self.target_qty is None else float(self.target_qty),
        )
        object.__setattr__(
            self,
            "conflict_resolution",
            {str(key): value for key, value in dict(self.conflict_resolution).items()},
        )

    def _payload_without_hashes(self) -> dict[str, object]:
        risk_decision = build_risk_decision_artifact(
            max_target_exposure_krw=self.conflict_resolution.get("exposure_cap_krw"),
            exposure_cap_source=str(self.conflict_resolution.get("exposure_cap_source", "none")),
            decision_context="portfolio_target",
        )
        return {
            "schema_version": int(self.schema_version),
            "pair": self.pair,
            "target_exposure_krw": self.target_exposure_krw,
            "max_target_exposure_krw": self.target_exposure_krw,
            "pre_cap_weighted_target_exposure_krw": self.conflict_resolution.get(
                "pre_cap_weighted_target_exposure_krw"
            ),
            "exposure_cap_krw": self.conflict_resolution.get("exposure_cap_krw"),
            "exposure_cap_applied": bool(self.conflict_resolution.get("exposure_cap_applied", False)),
            "exposure_cap_source": self.conflict_resolution.get("exposure_cap_source", "none"),
            "target_qty": self.target_qty,
            "allocator_policy_name": self.allocator_policy_name,
            "allocator_policy_version": self.allocator_policy_version,
            "allocator_config_hash": self.allocator_config_hash,
            "strategy_contribution_hash": self.strategy_contribution_hash,
            "allocation_input_hash": self.allocation_input_hash,
            "reason": self.reason,
            "conflict_resolution": dict(self.conflict_resolution),
            "authoritative": bool(self.authoritative),
            "fail_closed_reason": self.fail_closed_reason,
            "risk_budget_semantics": RISK_BUDGET_SEMANTICS,
            "risk_decision": risk_decision,
            "exposure_boundary_artifact": risk_decision,
            "exposure_boundary_artifact_hash": risk_decision["exposure_boundary_artifact_hash"],
            "risk_decision_hash": risk_decision["risk_decision_hash"],
            "risk_budget_legacy_marker": RISK_BUDGET_LEGACY_MARKER,
        }

    def content_hash(self) -> str:
        payload = self._payload_without_hashes()
        payload["final_portfolio_target_hash"] = sha256_prefixed(payload)
        portfolio_risk_decision = build_portfolio_risk_decision(payload)
        payload.update(
            {
                "portfolio_risk_decision": portfolio_risk_decision,
                "portfolio_risk_decision_hash": portfolio_risk_decision[
                    "portfolio_risk_decision_hash"
                ],
                "portfolio_risk_policy_hash": portfolio_risk_decision[
                    "portfolio_risk_policy_hash"
                ],
                "portfolio_risk_input_hash": portfolio_risk_decision[
                    "portfolio_risk_input_hash"
                ],
                "portfolio_risk_evidence_hash": portfolio_risk_decision[
                    "portfolio_risk_evidence_hash"
                ],
                "portfolio_risk_status": portfolio_risk_decision["status"],
                "portfolio_risk_reason_code": portfolio_risk_decision["reason_code"],
                "portfolio_risk_state_source": portfolio_risk_decision["state_source"],
            }
        )
        payload["final_portfolio_target_hash"] = sha256_prefixed(payload)
        return payload["final_portfolio_target_hash"]

    def as_dict(self) -> dict[str, object]:
        payload = self._payload_without_hashes()
        payload["final_portfolio_target_hash"] = sha256_prefixed(payload)
        portfolio_risk_decision = build_portfolio_risk_decision(payload)
        payload.update(
            {
                "portfolio_risk_decision": portfolio_risk_decision,
                "portfolio_risk_decision_hash": portfolio_risk_decision[
                    "portfolio_risk_decision_hash"
                ],
                "portfolio_risk_policy_hash": portfolio_risk_decision[
                    "portfolio_risk_policy_hash"
                ],
                "portfolio_risk_input_hash": portfolio_risk_decision[
                    "portfolio_risk_input_hash"
                ],
                "portfolio_risk_evidence_hash": portfolio_risk_decision[
                    "portfolio_risk_evidence_hash"
                ],
                "portfolio_risk_status": portfolio_risk_decision["status"],
                "portfolio_risk_reason_code": portfolio_risk_decision["reason_code"],
                "portfolio_risk_state_source": portfolio_risk_decision["state_source"],
            }
        )
        payload["final_portfolio_target_hash"] = sha256_prefixed(payload)
        return payload
