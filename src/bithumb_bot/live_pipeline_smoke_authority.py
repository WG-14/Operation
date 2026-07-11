from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import runtime_code_provenance, settings
from .artifact_hashing import sha256_prefixed
from .storage_io import write_json_atomic


LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE = "operator_live_pipeline_smoke_authority"
LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN = "LIVE_PIPELINE_SMOKE_5X_10000"
LIVE_PIPELINE_SMOKE_CYCLES = 5
LIVE_PIPELINE_SMOKE_MAX_ORDERS = 10
LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW = 10_000.0
LIVE_PIPELINE_SMOKE_ALLOWED_SEQUENCE = tuple(["BUY", "SELL"] * LIVE_PIPELINE_SMOKE_CYCLES)


class LivePipelineSmokeAuthorityError(ValueError):
    pass


@dataclass(frozen=True)
class LivePipelineSmokeAuthority:
    payload: dict[str, Any]
    path: Path | None = None

    @property
    def expires_at(self) -> datetime:
        raw = str(self.payload.get("expires_at") or "").strip()
        if not raw:
            raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_expires_at_missing")
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_expires_at_invalid") from exc

    def verify(
        self,
        *,
        now: datetime | None = None,
        market: str | None = None,
        db_path: str | None = None,
        account_key: str | None = None,
        code_commit_sha: str | None = None,
        cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
        max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
        max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    ) -> None:
        verify_live_pipeline_smoke_authority(
            self.payload,
            now=now,
            market=market,
            db_path=db_path,
            account_key=account_key,
            code_commit_sha=code_commit_sha,
            cycles=cycles,
            max_orders=max_orders,
            max_notional_krw=max_notional_krw,
        )

    def consume(
        self,
        *,
        consumed_at: datetime | None = None,
        market: str | None = None,
        db_path: str | None = None,
        account_key: str | None = None,
        code_commit_sha: str | None = None,
        cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
        max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
        max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
        run_id: str | None = None,
    ) -> None:
        self.verify(
            now=consumed_at,
            market=market,
            db_path=db_path,
            account_key=account_key,
            code_commit_sha=code_commit_sha,
            cycles=cycles,
            max_orders=max_orders,
            max_notional_krw=max_notional_krw,
        )
        if self.path is None:
            raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_path_required_for_consumption")
        consumed_payload = dict(self.payload)
        consumed_payload["consumed_at"] = (consumed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        consumed_payload["consumed_run_id"] = str(run_id or "")
        write_json_atomic(self.path, consumed_payload)


def _canonical_db_hash(db_path: str | None) -> str:
    return sha256_prefixed(str(Path(str(db_path or "")).expanduser().resolve()) if db_path else "")


def _account_hash_prefix(account_key: str | None) -> str:
    return sha256_prefixed(str(account_key or ""))[:24] if account_key else ""


def build_live_pipeline_smoke_authority_payload(
    *,
    expires_at: datetime,
    cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
    max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
    max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    market: str | None = None,
    db_path: str | None = None,
    account_key: str | None = None,
    code_commit_sha: str | None = None,
    one_shot_nonce: str | None = None,
) -> dict[str, Any]:
    sequence = list(["BUY", "SELL"] * int(cycles))
    commit = str(code_commit_sha or runtime_code_provenance().get("commit_sha") or "unavailable")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE,
        "authority_type": LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE,
        "strategy_performance_evidence": False,
        "promotion_evidence": False,
        "approved_profile_evidence": False,
        "promotion_grade": False,
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        "one_shot_nonce": str(one_shot_nonce or uuid.uuid4().hex),
        "consumed_at": None,
        "market": str(market if market is not None else settings.PAIR).strip().upper(),
        "db_path_hash": _canonical_db_hash(db_path if db_path is not None else settings.DB_PATH),
        "account_key_hash_prefix": _account_hash_prefix(account_key if account_key is not None else settings.BITHUMB_API_KEY),
        "code_commit_sha": commit,
        "max_notional_krw": float(max_notional_krw),
        "cycles": int(cycles),
        "max_orders": int(max_orders),
        "allowed_sequence": sequence,
        "operator_confirmation_required": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
    }
    payload["authority_content_hash"] = sha256_prefixed(
        {k: v for k, v in payload.items() if k != "authority_content_hash"}
    )
    return payload


def write_live_pipeline_smoke_authority(
    path: str | Path,
    *,
    cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
    max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
    max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    expires_min: int = 10,
) -> dict[str, Any]:
    payload = build_live_pipeline_smoke_authority_payload(
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=int(expires_min)),
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
    )
    write_json_atomic(Path(path).expanduser(), payload)
    return payload


def load_live_pipeline_smoke_authority(path: str | Path) -> LivePipelineSmokeAuthority:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_payload_not_object")
    return LivePipelineSmokeAuthority(payload, Path(path).expanduser())


def verify_live_pipeline_smoke_authority(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    market: str | None = None,
    db_path: str | None = None,
    account_key: str | None = None,
    code_commit_sha: str | None = None,
    cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
    max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
    max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
) -> None:
    if str(payload.get("artifact_type") or "") != LIVE_PIPELINE_SMOKE_AUTHORITY_ARTIFACT_TYPE:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_artifact_type_invalid")
    if payload.get("consumed_at"):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_reused")
    for key in ("strategy_performance_evidence", "promotion_evidence", "approved_profile_evidence"):
        if bool(payload.get(key)) is not False:
            raise LivePipelineSmokeAuthorityError(f"live_pipeline_smoke_authority_{key}_must_be_false")
    if bool(payload.get("promotion_grade")) is not False:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_promotion_grade_must_be_false")
    if str(payload.get("operator_confirmation_required") or "") != LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_confirmation_token_mismatch")
    if not str(payload.get("one_shot_nonce") or "").strip():
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_nonce_missing")
    if int(payload.get("cycles") or 0) != int(cycles):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_cycles_mismatch")
    if int(payload.get("max_orders") or 0) != int(max_orders):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_max_orders_mismatch")
    if float(max_notional_krw) > float(payload.get("max_notional_krw") or 0.0):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_notional_above_authority")
    if float(payload.get("max_notional_krw") or 0.0) != float(max_notional_krw):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_max_notional_mismatch")
    if tuple(str(item).upper() for item in payload.get("allowed_sequence") or ()) != LIVE_PIPELINE_SMOKE_ALLOWED_SEQUENCE:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_allowed_sequence_invalid")
    if market is not None and str(payload.get("market") or "").strip().upper() != str(market or "").strip().upper():
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_market_mismatch")
    if db_path is not None and str(payload.get("db_path_hash") or "") != _canonical_db_hash(db_path):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_db_path_mismatch")
    if account_key is not None and str(payload.get("account_key_hash_prefix") or "") != _account_hash_prefix(account_key):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_account_mismatch")
    if code_commit_sha is not None and str(payload.get("code_commit_sha") or "") != str(code_commit_sha or ""):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_code_commit_mismatch")
    expected = str(payload.get("authority_content_hash") or "")
    if not expected.startswith("sha256:"):
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_hash_missing")
    actual = sha256_prefixed({k: v for k, v in payload.items() if k != "authority_content_hash"})
    if actual != expected:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_hash_mismatch")
    check_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if LivePipelineSmokeAuthority(payload).expires_at <= check_now:
        raise LivePipelineSmokeAuthorityError("live_pipeline_smoke_authority_expired")


def build_live_pipeline_smoke_plan_payload(
    *,
    cycles: int,
    max_orders: int,
    max_notional_krw: float,
    market: str,
) -> dict[str, Any]:
    return {
        "status": "plan",
        "execution_mode": "live_pipeline_smoke",
        "cycles_requested": int(cycles),
        "round_trips_requested": int(cycles),
        "orders_expected": int(max_orders),
        "buy_expected": int(cycles),
        "sell_expected": int(cycles),
        "max_notional_krw": float(max_notional_krw),
        "market": str(market).strip().upper(),
        "allowed_sequence": list(["BUY", "SELL"] * int(cycles)),
        "requires_authority_path": True,
        "requires_confirmation": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
        "authority_command": "live-pipeline-smoke-authority",
        "execution_mode_metadata": {
            "execution_mode": "live_pipeline_smoke",
            "candle_checkpoint_authority": "smoke_step_checkpoint",
            "market_reference_source": "latest_closed_candle_or_top_of_book",
            "normal_h74_strategy_performance_authority": False,
        },
    }
