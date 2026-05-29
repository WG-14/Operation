from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROMOTION_ARTIFACT_GRADE = "promotion_candidate"
DIAGNOSTIC_ARTIFACT_GRADE = "diagnostic_only"
PROMOTION_AUTHORITY_PLANE = "typed_execution_plan_bundle"
PROMOTION_EXECUTION_EVIDENCE_SOURCE = "typed_execution_plan_bundle"
PROMOTION_NEXT_ACTION = "regenerate_with_typed_execution_authority"

LEGACY_DECISION_AUTHORITY_SOURCES = frozenset(
    {
        "legacy_context",
        "context",
        "decision_context",
        "diagnostic_context",
        "compatibility_context",
    }
)


@dataclass(frozen=True)
class PromotionArtifactProvenance:
    authority_plane: str
    decision_authority_source: str
    execution_evidence_source: str
    execution_plan_bundle_present: bool
    execution_plan_bundle_hash: str
    typed_execution_summary_present: bool
    execution_summary_hash: str
    execution_submit_plan_hash: str
    compatibility_fallback: bool
    legacy_context_planning_used: bool
    runtime_replay_planning_error: str | None
    artifact_grade: str
    promotion_rejection_reason: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PromotionArtifactProvenance":
        return cls(
            authority_plane=str(payload.get("authority_plane") or ""),
            decision_authority_source=str(payload.get("decision_authority_source") or ""),
            execution_evidence_source=str(payload.get("execution_evidence_source") or ""),
            execution_plan_bundle_present=payload.get("execution_plan_bundle_present") is True,
            execution_plan_bundle_hash=str(payload.get("execution_plan_bundle_hash") or ""),
            typed_execution_summary_present=payload.get("typed_execution_summary_present") is True,
            execution_summary_hash=str(payload.get("execution_summary_hash") or ""),
            execution_submit_plan_hash=str(payload.get("execution_submit_plan_hash") or ""),
            compatibility_fallback=payload.get("compatibility_fallback") is True,
            legacy_context_planning_used=payload.get("legacy_context_planning_used") is True,
            runtime_replay_planning_error=(
                str(payload.get("runtime_replay_planning_error"))
                if str(payload.get("runtime_replay_planning_error") or "").strip()
                else None
            ),
            artifact_grade=str(payload.get("artifact_grade") or ""),
            promotion_rejection_reason=str(payload.get("promotion_rejection_reason") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionProvenanceValidation:
    ok: bool
    reason_codes: tuple[str, ...]
    recommended_next_action: str
    provenance: PromotionArtifactProvenance

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "reason_codes": list(self.reason_codes),
            "recommended_next_action": self.recommended_next_action,
        }
        payload.update(self.provenance.as_dict())
        return payload


def validate_promotion_artifact_provenance(
    payload: dict[str, Any],
) -> PromotionProvenanceValidation:
    provenance = PromotionArtifactProvenance.from_payload(payload)
    failures = promotion_provenance_failure_codes(provenance)
    return PromotionProvenanceValidation(
        ok=not failures,
        reason_codes=tuple(failures),
        recommended_next_action="none" if not failures else PROMOTION_NEXT_ACTION,
        provenance=provenance,
    )


def promotion_provenance_failure_codes(
    provenance: PromotionArtifactProvenance,
) -> list[str]:
    failures: list[str] = []
    if provenance.compatibility_fallback:
        failures.append("canonical_promotion_compatibility_fallback")
    if provenance.legacy_context_planning_used:
        failures.append("canonical_promotion_legacy_context_planning")
    if not provenance.execution_plan_bundle_present:
        failures.append("canonical_promotion_execution_plan_bundle_missing")
    if not _valid_hash(provenance.execution_plan_bundle_hash):
        failures.append("canonical_promotion_execution_plan_bundle_hash_missing")
    if not provenance.typed_execution_summary_present:
        failures.append("canonical_promotion_typed_execution_summary_missing")
    if not _valid_hash(provenance.execution_summary_hash):
        failures.append("canonical_promotion_execution_summary_hash_missing")
    if not _valid_hash(provenance.execution_submit_plan_hash):
        failures.append("canonical_promotion_execution_submit_plan_hash_missing")
    if provenance.decision_authority_source.strip() in LEGACY_DECISION_AUTHORITY_SOURCES:
        failures.append("canonical_promotion_legacy_context_authority")
    if provenance.runtime_replay_planning_error:
        failures.append("canonical_promotion_runtime_replay_planning_error")
    if provenance.execution_evidence_source != PROMOTION_EXECUTION_EVIDENCE_SOURCE:
        failures.append("canonical_promotion_typed_execution_provenance_missing")
    if provenance.authority_plane != PROMOTION_AUTHORITY_PLANE:
        failures.append("canonical_promotion_typed_authority_plane_missing")
    if provenance.artifact_grade != PROMOTION_ARTIFACT_GRADE:
        failures.append("canonical_promotion_artifact_grade_not_promotion")
    if provenance.promotion_rejection_reason.strip():
        failures.append("canonical_promotion_rejection_reason_present")
    return sorted(set(failures))


def payload_has_promotion_provenance_markers(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "authority_plane",
            "decision_authority_source",
            "execution_evidence_source",
            "execution_plan_bundle_present",
            "execution_plan_bundle_hash",
            "typed_execution_summary_present",
            "execution_summary_hash",
            "execution_submit_plan_hash",
            "legacy_context_planning_used",
            "runtime_replay_planning_error",
            "artifact_grade",
            "promotion_rejection_reason",
        )
    )


def verify_promotion_provenance_artifact_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        validation = {
            "ok": False,
            "reason_codes": ["promotion_artifact_schema_not_object"],
            "recommended_next_action": PROMOTION_NEXT_ACTION,
        }
        return validation
    validation = validate_promotion_artifact_provenance(payload).as_dict()
    validation["artifact_path"] = str(Path(path).resolve())
    return validation


def _valid_hash(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith("sha256:") and len(text) > len("sha256:")
