from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

APPROVED_PROFILE_AUTHORITY_TYPE = "approved_profile_authority"
EMERGENCY_CLOSEOUT_AUTHORITY_TYPE = "emergency_closeout_authority"


@dataclass(frozen=True)
class ExecutionAuthority:
    authority_type: str
    allowed_operations: tuple[str, ...]
    market_scope: tuple[str, ...]
    notional_cap: float | None
    expires_at: str | None
    parameter_authority: bool
    exit_policy_authority: bool
    risk_authority: bool
    evidence_classification: str
    identity_hash: str

    def allows(self, operation: str) -> bool:
        return str(operation or "") in set(self.allowed_operations)


def _identity_hash(payload: Mapping[str, Any]) -> str:
    for key in ("authority_content_hash", "profile_content_hash", "identity_hash"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def execution_authority_from_payload(payload: Mapping[str, Any]) -> ExecutionAuthority:
    artifact_type = str(payload.get("artifact_type") or payload.get("authority_type") or "").strip()
    if artifact_type in {"approved_profile", APPROVED_PROFILE_AUTHORITY_TYPE} or payload.get("profile_content_hash"):
        market = str(payload.get("market") or payload.get("pair") or "*").strip().upper()
        return ExecutionAuthority(
            authority_type=APPROVED_PROFILE_AUTHORITY_TYPE,
            allowed_operations=("strategy_run", "strategy_live_dry_run", "small_live"),
            market_scope=(market,),
            notional_cap=None,
            expires_at=None,
            parameter_authority=True,
            exit_policy_authority=True,
            risk_authority=True,
            evidence_classification="approved_profile",
            identity_hash=_identity_hash(payload),
        )
    if artifact_type == EMERGENCY_CLOSEOUT_AUTHORITY_TYPE:
        return ExecutionAuthority(
            authority_type=EMERGENCY_CLOSEOUT_AUTHORITY_TYPE,
            allowed_operations=("position_reduction", "cancel_open_orders"),
            market_scope=(str(payload.get("market") or "*").strip().upper(),),
            notional_cap=float(payload.get("notional_cap") or 0.0) if payload.get("notional_cap") is not None else None,
            expires_at=str(payload.get("expires_at") or "") or None,
            parameter_authority=False,
            exit_policy_authority=False,
            risk_authority=True,
            evidence_classification="emergency_closeout",
            identity_hash=_identity_hash(payload),
        )
    raise ValueError(f"unknown_execution_authority_type:{artifact_type or 'missing'}")


def resolve_execution_authority(
    command_intent: str,
    settings: object,
    args_or_payload: Mapping[str, Any] | str | Path | object,
) -> ExecutionAuthority:
    del settings
    payload: Mapping[str, Any] | None = None
    if isinstance(args_or_payload, Mapping):
        payload = args_or_payload
    elif isinstance(args_or_payload, (str, Path)):
        with Path(args_or_payload).expanduser().open("r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        if not isinstance(decoded, Mapping):
            raise ValueError("execution_authority_payload_not_object")
        payload = decoded
    else:
        candidate = getattr(args_or_payload, "payload", None)
        if isinstance(candidate, Mapping):
            payload = candidate
    if payload is None:
        raise ValueError("execution_authority_payload_missing")
    authority = execution_authority_from_payload(payload)
    intent = str(command_intent or "").strip()
    operation = {
        "smoke-buy": "operator_smoke_buy",
        "operator_smoke_buy": "operator_smoke_buy",
        "strategy-run": "strategy_run",
        "strategy_run": "strategy_run",
    }.get(intent, intent)
    if operation:
        require_authority_operation(authority, operation)
    return authority


def require_authority_operation(authority: ExecutionAuthority, operation: str) -> None:
    if not authority.allows(operation):
        raise ValueError(
            "execution_authority_operation_not_allowed:"
            f"authority_type={authority.authority_type}:operation={operation}"
        )
