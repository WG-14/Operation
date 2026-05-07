from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .research.hashing import content_hash_payload, sha256_prefixed


DECISION_EQUIVALENCE_SCHEMA_VERSION = 1
DECISION_EQUIVALENCE_HASH_FIELD = "content_hash"
DECISION_EQUIVALENCE_HASH_EXCLUDED_FIELDS = frozenset({DECISION_EQUIVALENCE_HASH_FIELD, "generated_at"})
DECISION_KEYS = (
    "signal_timestamp",
    "candle_basis",
    "side",
    "strategy_name",
    "profile_content_hash",
    "market",
    "interval",
    "fee_model_hash",
    "slippage_model_hash",
    "blocked",
    "block_reason",
)


@dataclass(frozen=True)
class DecisionEquivalenceResult:
    report: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok"))


def compare_decision_equivalence(
    *,
    research_decisions: list[dict[str, Any]],
    runtime_decisions: list[dict[str, Any]],
    profile_hash: str,
    market: str,
    interval: str,
    data_fingerprint: str,
    generated_at: str | None = None,
) -> DecisionEquivalenceResult:
    research_by_key = {_decision_key(item): item for item in research_decisions}
    runtime_by_key = {_decision_key(item): item for item in runtime_decisions}
    mismatch_items: list[dict[str, object]] = []
    missing_research = sorted(set(runtime_by_key) - set(research_by_key))
    missing_runtime = sorted(set(research_by_key) - set(runtime_by_key))
    for key in sorted(set(research_by_key) & set(runtime_by_key)):
        left = research_by_key[key]
        right = runtime_by_key[key]
        field_mismatches = []
        for field in DECISION_KEYS:
            if _normalized(left.get(field)) != _normalized(right.get(field)):
                field_mismatches.append(
                    {"field": field, "research": left.get(field), "runtime": right.get(field)}
                )
        if field_mismatches:
            mismatch_items.append({"decision_key": key, "reason_code": "decision_field_mismatch", "fields": field_mismatches})
    reason_codes = []
    if missing_research:
        reason_codes.append("missing_research_decision")
    if missing_runtime:
        reason_codes.append("missing_runtime_decision")
    reason_codes.extend(_field_reason(item) for item in mismatch_items)
    report: dict[str, Any] = {
        "schema_version": DECISION_EQUIVALENCE_SCHEMA_VERSION,
        "ok": not reason_codes,
        "reason_codes": sorted(set(reason_codes)),
        "profile_content_hash": profile_hash,
        "market": market,
        "interval": interval,
        "data_fingerprint": data_fingerprint,
        "research_decision_count": len(research_decisions),
        "runtime_decision_count": len(runtime_decisions),
        "matched_decision_count": len(set(research_by_key) & set(runtime_by_key)) - len(mismatch_items),
        "mismatched_decision_count": len(mismatch_items),
        "missing_research_decisions": missing_research,
        "missing_runtime_decisions": missing_runtime,
        "mismatches": mismatch_items,
        "recommended_next_action": "none" if not reason_codes else "inspect_research_runtime_decision_drift_before_promotion",
        "generated_at": generated_at,
    }
    report[DECISION_EQUIVALENCE_HASH_FIELD] = compute_decision_equivalence_hash(report)
    return DecisionEquivalenceResult(report=report)


def compute_decision_equivalence_hash(report: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in report.items()
        if key not in DECISION_EQUIVALENCE_HASH_EXCLUDED_FIELDS
    }
    return sha256_prefixed(content_hash_payload(payload))


def load_decision_list(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and isinstance(payload.get("decisions"), list):
        payload = payload["decisions"]
    if not isinstance(payload, list):
        raise ValueError("decision_payload_not_list")
    decisions: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("decision_item_not_object")
        decisions.append(dict(item))
    return decisions


def _decision_key(item: dict[str, Any]) -> str:
    return "|".join(
        (
            str(item.get("signal_timestamp") or ""),
            str(item.get("market") or ""),
            str(item.get("interval") or ""),
        )
    )


def _field_reason(item: dict[str, object]) -> str:
    fields = item.get("fields")
    if not isinstance(fields, list) or not fields:
        return "decision_field_mismatch"
    first = fields[0]
    if not isinstance(first, dict):
        return "decision_field_mismatch"
    field = str(first.get("field") or "field")
    return f"decision_{field}_mismatch"


def _normalized(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value or "").strip()
