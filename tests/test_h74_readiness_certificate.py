from __future__ import annotations

import pytest

from bithumb_bot.h74_live_rehearsal import H74LiveRehearsalConfig, run_h74_live_rehearsal
from bithumb_bot.h74_readiness_certificate import (
    H74ReadinessCertificateError,
    build_h74_readiness_certificate,
    validate_h74_readiness_certificate,
)


def test_certificate_contains_commit_env_broker_order_rule_hashes(tmp_path) -> None:
    env = tmp_path / "live.env"
    env.write_text("MODE=live\n", encoding="utf-8")
    rehearsal = run_h74_live_rehearsal(H74LiveRehearsalConfig())

    cert = build_h74_readiness_certificate(rehearsal, env_file=str(env), expires_at_sec=9_999_999_999)

    assert cert["commit_sha"]
    assert cert["env_file_hash"].startswith("sha256:")
    assert cert["db_schema_hash"].startswith("sha256:")
    assert cert["h74_authority_hash"] == rehearsal["rehearsal_hash"]
    assert cert["broker_balance_snapshot_hash"] == rehearsal["broker_balance_snapshot_hash"]
    assert cert["order_rule_fee_authority_hash"].startswith("sha256:")
    assert cert["gate_trace_hash"] == rehearsal["gate_trace_hash"]
    assert cert["would_submit_plan_hash"] == rehearsal["would_submit_plan_hash"]


def test_certificate_invalid_when_env_hash_changes(tmp_path) -> None:
    env = tmp_path / "live.env"
    env.write_text("MODE=live\nA=1\n", encoding="utf-8")
    rehearsal = run_h74_live_rehearsal(H74LiveRehearsalConfig())
    cert = build_h74_readiness_certificate(rehearsal, env_file=str(env), expires_at_sec=9_999_999_999)

    env.write_text("MODE=live\nA=2\n", encoding="utf-8")
    verdict = validate_h74_readiness_certificate(
        cert,
        env_file=str(env),
        broker_balance_snapshot_hash=str(rehearsal["broker_balance_snapshot_hash"]),
        now_sec=1,
    )

    assert verdict["valid"] is False
    assert "env_hash_changed" in verdict["reasons"]


def test_certificate_not_issued_when_pre_submit_risk_blocks() -> None:
    rehearsal = run_h74_live_rehearsal(
        H74LiveRehearsalConfig(broker_snapshot_available=False)
    )

    with pytest.raises(H74ReadinessCertificateError, match="pre_submit_risk_status"):
        build_h74_readiness_certificate(rehearsal, env_file=None)
