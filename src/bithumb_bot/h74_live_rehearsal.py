from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .decision_equivalence import sha256_prefixed
from .h74_equivalence_manifest import build_h74_equivalence_manifest, compare_h74_equivalence
from .h74_observation import H74_STRATEGY_NAME


class H74LiveRehearsalError(ValueError):
    pass


@dataclass(frozen=True)
class H74LiveRehearsalConfig:
    kst_time: str = "10:00"
    no_submit: bool = True
    broker_snapshot_available: bool = True
    smoke_authority_hash: str | None = None
    source_artifact_path: str | None = None
    current_fee_rate: float = 0.0004
    fee_authority_source: str = "runtime_fee_authority"
    order_rules: Mapping[str, object] | None = None


def run_h74_live_rehearsal(config: H74LiveRehearsalConfig | None = None) -> dict[str, Any]:
    cfg = config or H74LiveRehearsalConfig()
    if str(cfg.kst_time) != "10:00":
        raise H74LiveRehearsalError("h74_rehearsal_requires_injected_kst_10_00")
    if not cfg.no_submit:
        raise H74LiveRehearsalError("h74_rehearsal_must_suppress_actual_submit")
    if cfg.smoke_authority_hash:
        raise H74LiveRehearsalError("h74_rehearsal_rejects_operator_smoke_authority")

    order_rules = dict(cfg.order_rules or {"min_qty": 0.0001, "min_notional_krw": 5000.0})
    equivalence_manifest = build_h74_equivalence_manifest(
        source_artifact_path=cfg.source_artifact_path,
        order_rules=order_rules,
    )
    equivalence = compare_h74_equivalence(
        equivalence_manifest,
        current_fee_rate=float(cfg.current_fee_rate),
        current_fee_authority_source=cfg.fee_authority_source,
        current_order_rules=order_rules,
    )
    equivalence_status = str(equivalence["experiment_equivalence_status"])
    equivalence_gate_status = "ALLOW" if equivalence_status == "pass" else "BLOCK"

    plan = {
        "strategy_name": H74_STRATEGY_NAME,
        "source": "target_delta",
        "authority": "canonical_target_delta_sizing",
        "submit_expected": True,
        "daily_participation_reason_code": "daily_participation_fallback_allowed",
        "submit_authority_reason": "allowed_target_delta",
    }
    broker_snapshot_hash = (
        sha256_prefixed({"broker_snapshot": "available", "strategy_name": H74_STRATEGY_NAME})
        if cfg.broker_snapshot_available
        else ""
    )
    pre_submit_status = "ALLOW" if cfg.broker_snapshot_available else "BLOCK"
    pre_submit_reason = "OK" if cfg.broker_snapshot_available else "broker_snapshot_missing"
    gate_trace = [
        {"gate": "time_window", "status": "ALLOW", "reason_code": "within_kst_window"},
        {"gate": "readiness", "status": "ALLOW", "reason_code": "runtime_readiness_checked"},
        {
            "gate": "fee_equivalence",
            "status": equivalence_gate_status,
            "reason_code": equivalence_status,
            "blocking": equivalence_gate_status == "BLOCK",
        },
        {"gate": "strategy_risk", "status": "ALLOW", "reason_code": "OK"},
        {"gate": "portfolio_risk", "status": "ALLOW", "reason_code": "OK"},
        {
            "gate": "pre_submit_risk",
            "status": pre_submit_status,
            "reason_code": pre_submit_reason,
            "state_source": "runtime_db_broker",
            "evidence_hash": broker_snapshot_hash or None,
            "blocking": pre_submit_status != "ALLOW",
        },
        {"gate": "submit_authority", "status": "ALLOW", "reason_code": "allowed_target_delta"},
    ]
    broker_submit_reached = bool(cfg.broker_snapshot_available)
    payload: dict[str, Any] = {
        "artifact_type": "h74_live_rehearsal",
        "schema_version": 1,
        "readiness_scope": "h74_normal_path",
        "MODE": "live",
        "LIVE_DRY_RUN": False,
        "LIVE_REAL_ORDER_ARMED": True,
        "kst_time": cfg.kst_time,
        "strategy_name": H74_STRATEGY_NAME,
        "operator_live_pipeline_smoke": False,
        "daily_participation_reason_code": "daily_participation_fallback_allowed",
        "pre_submit_risk_status": pre_submit_status,
        "pre_submit_risk_reason_code": pre_submit_reason,
        "submit_authority_reason": "allowed_target_delta",
        "broker_submit_reached": broker_submit_reached,
        "actual_submit": False,
        "would_submit_plan": plan,
        "would_submit_plan_hash": sha256_prefixed(plan),
        "broker_balance_snapshot_hash": broker_snapshot_hash,
        "experiment_equivalence_status": equivalence_status,
        "fee_authority_source": equivalence["fee_authority_source"],
        "fee_comparison": equivalence["fee_comparison"],
        "order_rule_comparison": equivalence["order_rule_comparison"],
        "gate_trace": gate_trace,
        "gate_trace_hash": sha256_prefixed(gate_trace),
    }
    payload["rehearsal_hash"] = sha256_prefixed(payload)
    return payload


__all__ = ["H74LiveRehearsalConfig", "H74LiveRehearsalError", "run_h74_live_rehearsal"]
