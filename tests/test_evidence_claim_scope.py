from __future__ import annotations

import pytest

from bithumb_bot.evidence_claim_scope import EvidenceArtifactType, require_artifact_claim_scope


def test_pipeline_smoke_rejected_as_decision_parity_evidence() -> None:
    payload = {
        "artifact_type": "BrokerPipelineSmokeEvidence",
        "claims_scope": "operator_pipeline_only",
        "ok": True,
    }

    with pytest.raises(ValueError, match="evidence_artifact_type_mismatch"):
        require_artifact_claim_scope(payload, required_type=EvidenceArtifactType.DECISION_PARITY)


def test_h74_rehearsal_rejected_as_live_submit_evidence() -> None:
    payload = {
        "artifact_type": "SyntheticGateEvidence",
        "claims_scope": "synthetic_gate",
        "full_lifecycle_equivalence_supported": False,
    }

    with pytest.raises(ValueError, match="evidence_artifact_type_mismatch"):
        require_artifact_claim_scope(payload, required_type=EvidenceArtifactType.LIVE_SUBMIT)


def test_decision_equivalence_rejected_as_full_lifecycle_without_fill_claims() -> None:
    payload = {
        "artifact_type": "DecisionParityEvidence",
        "claims_scope": "submit_plan_equivalence_only",
        "submit_plan_equivalence_supported": True,
        "full_lifecycle_equivalence_supported": False,
    }

    with pytest.raises(ValueError, match="evidence_artifact_type_mismatch"):
        require_artifact_claim_scope(payload, required_type=EvidenceArtifactType.FULL_LIFECYCLE_COMPARISON)


def test_paired_diagnostic_rejected_as_full_lifecycle_without_fill_claims() -> None:
    payload = {
        "artifact_type": "PairedExperimentEvidence",
        "claims_scope": "paired_diagnostic_only",
        "full_lifecycle_equivalence_supported": False,
    }

    with pytest.raises(ValueError, match="evidence_artifact_type_mismatch"):
        require_artifact_claim_scope(payload, required_type=EvidenceArtifactType.FULL_LIFECYCLE_COMPARISON)


def test_paired_experiment_artifact_accepted_for_paired_diagnostic_only() -> None:
    payload = {
        "artifact_type": "PairedExperimentEvidence",
        "claims_scope": "paired_diagnostic_only",
        "full_lifecycle_equivalence_supported": False,
    }

    scope = require_artifact_claim_scope(
        payload,
        required_type=EvidenceArtifactType.PAIRED_EXPERIMENT,
        allow_diagnostic_only=True,
    )

    assert scope.claims_scope == "paired_diagnostic_only"


def test_missing_artifact_type_fails_closed() -> None:
    with pytest.raises(ValueError, match="evidence_artifact_type_missing"):
        require_artifact_claim_scope({"ok": True}, required_type=EvidenceArtifactType.DECISION_PARITY)
