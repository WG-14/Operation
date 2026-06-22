from __future__ import annotations

import pytest

from bithumb_bot.h74_live_rehearsal import (
    H74LiveRehearsalConfig,
    H74LiveRehearsalError,
    run_h74_live_rehearsal,
)


def test_h74_rehearsal_reaches_broker_submit_boundary_at_kst_10() -> None:
    payload = run_h74_live_rehearsal(H74LiveRehearsalConfig(kst_time="10:00", no_submit=True))

    assert payload["strategy_name"] == "daily_participation_sma"
    assert payload["daily_participation_reason_code"] == "daily_participation_fallback_allowed"
    assert payload["pre_submit_risk_status"] == "ALLOW"
    assert payload["submit_authority_reason"] == "allowed_target_delta"
    assert payload["broker_submit_reached"] is True
    assert payload["actual_submit"] is False
    assert payload["LIVE_DRY_RUN"] is False


def test_h74_rehearsal_does_not_use_operator_smoke_authority() -> None:
    payload = run_h74_live_rehearsal(H74LiveRehearsalConfig())

    assert payload["operator_live_pipeline_smoke"] is False
    assert "operator_live_pipeline_smoke" not in payload["would_submit_plan"]
    with pytest.raises(H74LiveRehearsalError, match="rejects_operator_smoke_authority"):
        run_h74_live_rehearsal(H74LiveRehearsalConfig(smoke_authority_hash="sha256:smoke"))


def test_h74_rehearsal_fails_when_pre_submit_broker_snapshot_missing() -> None:
    payload = run_h74_live_rehearsal(
        H74LiveRehearsalConfig(broker_snapshot_available=False)
    )

    assert payload["pre_submit_risk_status"] == "BLOCK"
    assert payload["pre_submit_risk_reason_code"] == "broker_snapshot_missing"
    assert payload["broker_submit_reached"] is False


def test_h74_rehearsal_does_not_accept_smoke_proof_as_pre_submit_proof() -> None:
    with pytest.raises(H74LiveRehearsalError, match="rejects_operator_smoke_authority"):
        run_h74_live_rehearsal(H74LiveRehearsalConfig(smoke_authority_hash="sha256:smoke"))


def test_rehearsal_reports_fee_equivalence_gate(tmp_path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        '{"runtime_base_cost_assumption":{"fee_rate":0.0004,"slippage_bps":10},"candle_timing":"closed_candle_kst"}',
        encoding="utf-8",
    )
    payload = run_h74_live_rehearsal(
        H74LiveRehearsalConfig(
            source_artifact_path=str(source),
            current_fee_rate=0.0025,
            fee_authority_source="chance_doc",
        )
    )

    assert payload["experiment_equivalence_status"] == "mismatch"
    assert payload["fee_authority_source"] == "chance_doc"
    gate = [entry for entry in payload["gate_trace"] if entry["gate"] == "fee_equivalence"][0]
    assert gate["status"] == "BLOCK"
    assert gate["reason_code"] == "mismatch"
