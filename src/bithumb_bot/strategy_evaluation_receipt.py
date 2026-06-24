from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .decision_equivalence import sha256_prefixed
from .strategy_policy_contract import StrategyDecisionV2


SERVICE_BOUNDARY_ID = "StrategyDecisionService.evaluate"


@dataclass(frozen=True)
class StrategyEvaluationReceipt:
    service_boundary_id: str
    service_evaluation_hash: str
    decision_input_bundle_hash: str
    policy_decision_hash: str
    strategy_name: str
    mode: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_strategy_evaluation_receipt(
    *,
    decision_input_bundle_hash: str,
    policy_decision_hash: str,
    strategy_name: str,
    mode: str,
) -> StrategyEvaluationReceipt:
    payload = {
        "service_boundary_id": SERVICE_BOUNDARY_ID,
        "decision_input_bundle_hash": str(decision_input_bundle_hash or ""),
        "policy_decision_hash": str(policy_decision_hash or ""),
        "strategy_name": str(strategy_name or "").strip().lower(),
        "mode": str(mode or "").strip().lower(),
    }
    return StrategyEvaluationReceipt(
        **payload,
        service_evaluation_hash=sha256_prefixed(payload),
    )


def validate_strategy_evaluation_receipt(
    *,
    receipt: Mapping[str, object] | None,
    decision: StrategyDecisionV2,
    expected_input_bundle_hash: str | None = None,
    expected_strategy_name: str | None = None,
    expected_mode: str | None = None,
) -> None:
    if not isinstance(receipt, Mapping):
        raise ValueError("strategy_evaluation_receipt_missing")
    service_boundary_id = str(receipt.get("service_boundary_id") or "")
    if service_boundary_id != SERVICE_BOUNDARY_ID:
        raise ValueError("strategy_evaluation_receipt_boundary_invalid")
    policy_decision_hash = str(receipt.get("policy_decision_hash") or "")
    if policy_decision_hash != str(decision.policy_decision_hash or ""):
        raise ValueError("strategy_evaluation_receipt_policy_decision_hash_mismatch")
    input_bundle_hash = str(receipt.get("decision_input_bundle_hash") or "")
    if expected_input_bundle_hash is not None and input_bundle_hash != str(expected_input_bundle_hash or ""):
        raise ValueError("strategy_evaluation_receipt_input_bundle_hash_mismatch")
    strategy_name = str(receipt.get("strategy_name") or "").strip().lower()
    if expected_strategy_name is not None and strategy_name != str(expected_strategy_name or "").strip().lower():
        raise ValueError("strategy_evaluation_receipt_strategy_name_mismatch")
    mode = str(receipt.get("mode") or "").strip().lower()
    if expected_mode is not None and mode != str(expected_mode or "").strip().lower():
        raise ValueError("strategy_evaluation_receipt_mode_mismatch")
    expected_hash = sha256_prefixed(
        {
            "service_boundary_id": service_boundary_id,
            "decision_input_bundle_hash": input_bundle_hash,
            "policy_decision_hash": policy_decision_hash,
            "strategy_name": strategy_name,
            "mode": mode,
        }
    )
    if str(receipt.get("service_evaluation_hash") or "") != expected_hash:
        raise ValueError("strategy_evaluation_receipt_service_hash_mismatch")


__all__ = [
    "SERVICE_BOUNDARY_ID",
    "StrategyEvaluationReceipt",
    "build_strategy_evaluation_receipt",
    "validate_strategy_evaluation_receipt",
]
