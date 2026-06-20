from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bithumb_bot.live_pipeline_smoke_authority import (
    LIVE_PIPELINE_SMOKE_ALLOWED_SEQUENCE,
    LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE,
    LivePipelineSmokeAuthorityError,
    build_live_pipeline_smoke_authority_payload,
    verify_live_pipeline_smoke_authority,
)


def _payload(**overrides):
    payload = build_live_pipeline_smoke_authority_payload(
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        market="KRW-BTC",
        db_path="/tmp/live.sqlite",
        account_key="account",
        code_commit_sha="commit",
        cycles=5,
        max_orders=10,
        max_notional_krw=10_000.0,
    )
    payload.update(overrides)
    if overrides:
        from bithumb_bot.research.hashing import sha256_prefixed

        payload["authority_content_hash"] = sha256_prefixed(
            {k: v for k, v in payload.items() if k != "authority_content_hash"}
        )
    return payload


def _verify(payload):
    verify_live_pipeline_smoke_authority(
        payload,
        market="KRW-BTC",
        db_path="/tmp/live.sqlite",
        account_key="account",
        code_commit_sha="commit",
        cycles=5,
        max_orders=10,
        max_notional_krw=10_000.0,
    )


def test_authority_payload_binds_required_sequence() -> None:
    payload = _payload()

    assert payload["artifact_type"] == LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE
    assert tuple(payload["allowed_sequence"]) == LIVE_PIPELINE_SMOKE_ALLOWED_SEQUENCE
    _verify(payload)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}, "expired"),
        ({"consumed_at": datetime.now(timezone.utc).isoformat()}, "reused"),
        ({"market": "KRW-ETH"}, "market_mismatch"),
        ({"cycles": 4, "allowed_sequence": ["BUY", "SELL"] * 4}, "cycles_mismatch"),
        ({"max_orders": 8}, "max_orders_mismatch"),
        ({"max_notional_krw": 9_000.0}, "notional_above_authority|max_notional_mismatch"),
        ({"code_commit_sha": "other"}, "code_commit_mismatch"),
        ({"allowed_sequence": ["BUY"] * 10}, "allowed_sequence_invalid"),
    ],
)
def test_authority_rejects_bound_mismatches(overrides, match) -> None:
    with pytest.raises(LivePipelineSmokeAuthorityError, match=match):
        _verify(_payload(**overrides))


def test_authority_rejects_db_and_account_mismatch() -> None:
    with pytest.raises(LivePipelineSmokeAuthorityError, match="db_path_mismatch"):
        verify_live_pipeline_smoke_authority(
            _payload(),
            market="KRW-BTC",
            db_path="/tmp/other.sqlite",
            account_key="account",
            code_commit_sha="commit",
            cycles=5,
            max_orders=10,
            max_notional_krw=10_000.0,
        )

    with pytest.raises(LivePipelineSmokeAuthorityError, match="account_mismatch"):
        verify_live_pipeline_smoke_authority(
            _payload(),
            market="KRW-BTC",
            db_path="/tmp/live.sqlite",
            account_key="other",
            code_commit_sha="commit",
            cycles=5,
            max_orders=10,
            max_notional_krw=10_000.0,
        )
