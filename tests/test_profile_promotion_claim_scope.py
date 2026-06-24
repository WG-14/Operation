from __future__ import annotations

import pytest

from bithumb_bot.approved_profile import (
    ApprovedProfileError,
    _reject_evidence_artifact_contract_reasons,
    content_hash_payload,
    sha256_prefixed,
    verify_promotion_artifact,
)


def _with_content_hash(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["content_hash"] = sha256_prefixed(content_hash_payload(result))
    return result


def test_profile_promote_rejects_pipeline_smoke_evidence() -> None:
    payload = _with_content_hash(
        {
            "artifact_type": "BrokerPipelineSmokeEvidence",
            "claims_scope": "operator_pipeline_only",
            "status": "passed",
            "ok": True,
            "candidate_profile": {},
        }
    )

    with pytest.raises(ApprovedProfileError, match="promotion_claim_scope_invalid"):
        verify_promotion_artifact(payload)


def test_profile_promote_rejects_h74_synthetic_gate_as_live_submit() -> None:
    payload = {
        "artifact_type": "SyntheticGateEvidence",
        "claims_scope": "synthetic_gate",
        "full_lifecycle_equivalence_supported": True,
        "ok": True,
    }

    with pytest.raises(ApprovedProfileError, match="live_readiness_evidence_claim_scope_invalid"):
        _reject_evidence_artifact_contract_reasons(payload, label="live_readiness_evidence")


def test_profile_promote_rejects_missing_artifact_type() -> None:
    payload = _with_content_hash(
        {
            "claims_scope": "submit_plan_equivalence_only",
            "ok": True,
            "candidate_profile": {},
        }
    )

    with pytest.raises(ApprovedProfileError, match="evidence_artifact_type_missing"):
        verify_promotion_artifact(payload)
