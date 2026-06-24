from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from bithumb_bot.paired_experiment import PairedExperimentRun, run_paired_experiment
from bithumb_bot.paired_experiment_diff import PAIRED_EXPERIMENT_STAGE_ORDER


def _run(*, submit_enabled: bool = False) -> PairedExperimentRun:
    return PairedExperimentRun(
        run_id="paired-unit",
        candle_ts=1_704_046_800_000,
        market_snapshot_hash="sha256:market",
        profile_hash="sha256:profile",
        strategy_parameters_hash="sha256:parameters",
        shadow_initial_state_hash="sha256:shadow-state",
        actual_state_snapshot_hash="sha256:actual-state",
        submit_enabled=submit_enabled,
    )


def _lane(run: PairedExperimentRun) -> dict[str, object]:
    return {
        "candle_ts": run.candle_ts,
        "stages": {stage: {"hash": f"sha256:{stage}", "status": "ok"} for stage in PAIRED_EXPERIMENT_STAGE_ORDER},
    }


def test_paired_run_uses_same_closed_candle_for_both_lanes() -> None:
    with pytest.raises(ValueError, match="operational_candle_ts_mismatch"):
        run_paired_experiment(
            _run(),
            shadow_lane_runner=_lane,
            operational_lane_runner=lambda run: {**_lane(run), "candle_ts": run.candle_ts + 60_000},
        )


def test_shadow_lane_does_not_write_live_orders_or_fills() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY)")

    def shadow(run: PairedExperimentRun) -> dict[str, object]:
        del run
        return _lane(_run())

    run_paired_experiment(_run(), shadow_lane_runner=shadow, operational_lane_runner=_lane)

    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_operational_lane_can_run_read_only_without_submit() -> None:
    submit = Mock()

    artifact = run_paired_experiment(
        _run(submit_enabled=False),
        shadow_lane_runner=_lane,
        operational_lane_runner=_lane,
        broker_submit=submit,
    )

    assert artifact["operational_lane"]["submit_enabled"] is False
    assert submit.call_count == 0


def test_paired_run_artifact_contains_required_hashes() -> None:
    artifact = run_paired_experiment(_run(), shadow_lane_runner=_lane, operational_lane_runner=_lane)

    for key in (
        "shadow_lane",
        "operational_lane",
        "first_divergence",
        "market_snapshot_hash",
        "candle_ts",
        "shadow_initial_state_hash",
        "actual_state_snapshot_hash",
    ):
        assert key in artifact
