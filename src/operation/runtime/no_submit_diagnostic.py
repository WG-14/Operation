from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class RuntimeCycleDiagnostic:
    cycle_id: str
    candle_ts: int | None
    stage: str
    reason_code: str
    runtime_data_availability_report_hash: str | None = None
    strategy_decision_hash: str | None = None
    daily_count_snapshot_hash: str | None = None
    execution_plan_bundle_hash: str | None = None
    submit_authority_reason: str | None = None
    missing_because: Mapping[str, str] = field(default_factory=dict)

    @property
    def upstream_hashes(self) -> dict[str, object]:
        return {
            "runtime_data_availability_report_hash": self.runtime_data_availability_report_hash
            or self.missing_because.get("runtime_data_availability_report_hash"),
            "strategy_decision_hash": self.strategy_decision_hash
            or self.missing_because.get("strategy_decision_hash"),
            "daily_count_snapshot_hash": self.daily_count_snapshot_hash
            or self.missing_because.get("daily_count_snapshot_hash"),
            "execution_plan_bundle_hash": self.execution_plan_bundle_hash
            or self.missing_because.get("execution_plan_bundle_hash"),
        }

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["artifact_type"] = "runtime_cycle_no_submit_diagnostic"
        payload["schema_version"] = 1
        payload["upstream_hashes"] = self.upstream_hashes
        return payload


def diagnostic_for_stage(
    *,
    cycle_id: str,
    candle_ts: int | None,
    stage: str,
    reason_code: str,
    runtime_data_availability_report_hash: str | None = None,
    strategy_decision_hash: str | None = None,
    daily_count_snapshot_hash: str | None = None,
    execution_plan_bundle_hash: str | None = None,
    submit_authority_reason: str | None = None,
) -> RuntimeCycleDiagnostic:
    missing = {}
    for key, value in {
        "runtime_data_availability_report_hash": runtime_data_availability_report_hash,
        "strategy_decision_hash": strategy_decision_hash,
        "daily_count_snapshot_hash": daily_count_snapshot_hash,
        "execution_plan_bundle_hash": execution_plan_bundle_hash,
    }.items():
        if not str(value or "").strip():
            missing[key] = stage
    return RuntimeCycleDiagnostic(
        cycle_id=cycle_id,
        candle_ts=candle_ts,
        stage=stage,
        reason_code=reason_code,
        runtime_data_availability_report_hash=runtime_data_availability_report_hash,
        strategy_decision_hash=strategy_decision_hash,
        daily_count_snapshot_hash=daily_count_snapshot_hash,
        execution_plan_bundle_hash=execution_plan_bundle_hash,
        submit_authority_reason=submit_authority_reason,
        missing_because=missing,
    )


__all__ = ["RuntimeCycleDiagnostic", "diagnostic_for_stage"]
