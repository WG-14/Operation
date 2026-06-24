from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


PAIRED_EXPERIMENT_STAGE_ORDER = (
    "market_input",
    "feature_projection",
    "position_snapshot",
    "daily_count_snapshot",
    "strategy_decision",
    "risk_decision",
    "portfolio_target",
    "execution_plan",
    "submit_authority",
    "broker_payload",
    "fill",
    "accounting",
)


@dataclass(frozen=True)
class FirstDivergence:
    stage: str
    reason_code: str
    shadow_hash: str
    operational_hash: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def compare_paired_experiment_stages(
    shadow_lane: Mapping[str, object],
    operational_lane: Mapping[str, object],
) -> dict[str, object]:
    stage_diffs: list[dict[str, object]] = []
    first: FirstDivergence | None = None
    for stage in PAIRED_EXPERIMENT_STAGE_ORDER:
        shadow = _stage_payload(shadow_lane, stage)
        operational = _stage_payload(operational_lane, stage)
        shadow_hash = str(shadow.get("hash") or shadow.get("stage_hash") or "")
        operational_hash = str(operational.get("hash") or operational.get("stage_hash") or "")
        shadow_status = str(shadow.get("status") or "ok")
        operational_status = str(operational.get("status") or "ok")
        reason_code = str(shadow.get("reason_code") or operational.get("reason_code") or "")
        status = "match"
        if first is not None:
            status = "not_evaluated_after_divergence"
        elif shadow_status != "ok" or operational_status != "ok":
            status = "diverged"
            reason_code = reason_code or f"{stage}_status_not_ok"
        elif shadow_hash != operational_hash:
            status = "diverged"
            reason_code = reason_code or f"{stage}_hash_mismatch"
        row = {
            "stage": stage,
            "shadow_hash": shadow_hash,
            "operational_hash": operational_hash,
            "status": status,
            "reason_code": reason_code if status != "match" else "",
        }
        if status == "diverged" and first is None:
            first = FirstDivergence(
                stage=stage,
                reason_code=str(row["reason_code"]),
                shadow_hash=shadow_hash,
                operational_hash=operational_hash,
            )
        stage_diffs.append(row)
    return {
        "first_divergence": first.as_dict()
        if first is not None
        else {
            "stage": "",
            "reason_code": "",
            "shadow_hash": "",
            "operational_hash": "",
        },
        "stage_diffs": stage_diffs,
        "ok": first is None,
    }


def _stage_payload(lane: Mapping[str, object], stage: str) -> Mapping[str, object]:
    stages = lane.get("stages")
    if isinstance(stages, Mapping) and isinstance(stages.get(stage), Mapping):
        return stages[stage]  # type: ignore[return-value]
    value = lane.get(stage)
    if isinstance(value, Mapping):
        return value
    return {}


__all__ = ["FirstDivergence", "PAIRED_EXPERIMENT_STAGE_ORDER", "compare_paired_experiment_stages"]
