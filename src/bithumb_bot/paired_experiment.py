from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Mapping

from bithumb_bot.paired_experiment_diff import compare_paired_experiment_stages


LaneRunner = Callable[["PairedExperimentRun"], Mapping[str, object]]


@dataclass(frozen=True)
class PairedExperimentRun:
    run_id: str
    candle_ts: int
    market_snapshot_hash: str
    profile_hash: str
    strategy_parameters_hash: str
    shadow_initial_state_hash: str
    actual_state_snapshot_hash: str
    submit_enabled: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def run_paired_experiment(
    run: PairedExperimentRun,
    *,
    shadow_lane_runner: LaneRunner,
    operational_lane_runner: LaneRunner,
    broker_submit: Callable[..., object] | None = None,
) -> dict[str, object]:
    shadow_lane = dict(shadow_lane_runner(run))
    operational_lane = dict(operational_lane_runner(run))
    if not bool(run.submit_enabled) and broker_submit is not None:
        operational_lane.setdefault("submit_enabled", False)
    if bool(run.submit_enabled) and broker_submit is not None:
        broker_submit(run=run)
    if int(shadow_lane.get("candle_ts") or run.candle_ts) != int(run.candle_ts):
        raise ValueError("paired_experiment_shadow_candle_ts_mismatch")
    if int(operational_lane.get("candle_ts") or run.candle_ts) != int(run.candle_ts):
        raise ValueError("paired_experiment_operational_candle_ts_mismatch")
    diff = compare_paired_experiment_stages(shadow_lane, operational_lane)
    return {
        "artifact_type": "PairedExperimentEvidence",
        "claims_scope": "paired_diagnostic_only",
        "run": run.as_dict(),
        "run_id": run.run_id,
        "candle_ts": int(run.candle_ts),
        "market_snapshot_hash": run.market_snapshot_hash,
        "profile_hash": run.profile_hash,
        "strategy_parameters_hash": run.strategy_parameters_hash,
        "shadow_initial_state_hash": run.shadow_initial_state_hash,
        "actual_state_snapshot_hash": run.actual_state_snapshot_hash,
        "shadow_lane": shadow_lane,
        "operational_lane": operational_lane,
        "first_divergence": diff["first_divergence"],
        "stage_diffs": diff["stage_diffs"],
    }


__all__ = ["PairedExperimentRun", "run_paired_experiment"]
