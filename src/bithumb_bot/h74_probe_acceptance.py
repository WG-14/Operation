from __future__ import annotations

from collections.abc import Mapping


def evaluate_h74_execution_path_probe_acceptance(evidence: Mapping[str, object]) -> dict[str, object]:
    required = (
        "buy_decision",
        "buy_execution_plan",
        "buy_order_event",
        "buy_fill",
        "open_lot",
        "sell_decision",
        "sell_execution_plan",
        "sell_order_event",
        "sell_fill",
        "closed_trade_lifecycle",
        "final_flat_or_documented_dust",
    )
    missing = [key for key in required if not bool(evidence.get(key))]
    status = "PASS" if not missing else "INCOMPLETE"
    return {
        "artifact_type": "h74_execution_path_probe_acceptance",
        "acceptance_track": "execution_path_probe",
        "execution_path_probe_status": status,
        "missing_evidence": missing,
        "research_equivalence": False,
        "research_equivalence_status": "NOT_APPLICABLE",
        "production_approval": False,
        "promotion_grade": False,
    }
