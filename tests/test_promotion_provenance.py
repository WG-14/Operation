from __future__ import annotations

import json

from bithumb_bot.profile_cli import cmd_promotion_provenance_verify
from bithumb_bot.promotion_provenance import validate_promotion_artifact_provenance


def _typed_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "authority_plane": "typed_execution_plan_bundle",
        "decision_authority_source": "DecisionEnvelope.strategy_decision",
        "execution_evidence_source": "typed_execution_plan_bundle",
        "execution_plan_bundle_present": True,
        "execution_plan_bundle_hash": "sha256:bundle",
        "typed_execution_summary_present": True,
        "execution_summary_hash": "sha256:summary",
        "execution_submit_plan_hash": "sha256:submit-or-no-submit-proof",
        "compatibility_fallback": False,
        "legacy_context_planning_used": False,
        "runtime_replay_planning_error": "",
        "artifact_grade": "promotion_candidate",
        "promotion_rejection_reason": "",
    }
    payload.update(overrides)
    return payload


def test_promotion_provenance_contract_accepts_typed_authority_only() -> None:
    result = validate_promotion_artifact_provenance(_typed_payload())

    assert result.ok is True
    assert result.reason_codes == ()
    assert result.recommended_next_action == "none"


def test_promotion_rejects_legacy_context_planning() -> None:
    result = validate_promotion_artifact_provenance(
        _typed_payload(
            legacy_context_planning_used=True,
            compatibility_fallback=True,
            authority_plane="compatibility_context",
            execution_evidence_source="diagnostic_context_fallback",
            artifact_grade="diagnostic_only",
            promotion_rejection_reason="legacy_context_planning_diagnostic_only",
        )
    )

    assert result.ok is False
    assert "canonical_promotion_legacy_context_planning" in result.reason_codes
    assert "canonical_promotion_compatibility_fallback" in result.reason_codes
    assert "canonical_promotion_typed_execution_provenance_missing" in result.reason_codes
    assert result.recommended_next_action == "regenerate_with_typed_execution_authority"


def test_promotion_provenance_verify_cli_reports_structured_failure(tmp_path, capsys) -> None:
    artifact = tmp_path / "canonical.json"
    artifact.write_text(
        json.dumps(
            _typed_payload(
                execution_plan_bundle_hash="",
                runtime_replay_planning_error="runtime_replay_execution_readiness_unavailable",
            )
        ),
        encoding="utf-8",
    )

    rc = cmd_promotion_provenance_verify(artifact_path=str(artifact))

    captured = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert captured["ok"] is False
    assert captured["authority_plane"] == "typed_execution_plan_bundle"
    assert captured["execution_evidence_source"] == "typed_execution_plan_bundle"
    assert captured["execution_plan_bundle_hash"] == ""
    assert captured["typed_execution_summary_present"] is True
    assert captured["compatibility_fallback"] is False
    assert captured["legacy_context_planning_used"] is False
    assert captured["artifact_grade"] == "promotion_candidate"
    assert captured["promotion_rejection_reason"] == ""
    assert "canonical_promotion_execution_plan_bundle_hash_missing" in captured["reason_codes"]
    assert "canonical_promotion_runtime_replay_planning_error" in captured["reason_codes"]
    assert captured["recommended_next_action"] == "regenerate_with_typed_execution_authority"
