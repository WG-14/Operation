from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bithumb_bot import paired_experiment
from bithumb_bot.paired_experiment import (
    PairedExperimentRun,
    operational_runtime_lane,
    run_closed_candle_paired_experiment,
    run_paired_experiment,
    shadow_backtest_lane,
)
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


def test_paired_run_uses_runtime_closed_candle_snapshot() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE candles(ts INTEGER, pair TEXT, interval TEXT, close REAL)")
    conn.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY)")
    conn.execute(
        "INSERT INTO candles(ts, pair, interval, close) VALUES (?, ?, ?, ?)",
        (1_704_046_800_000, "KRW-BTC", "1m", 100.0),
    )

    artifact = run_closed_candle_paired_experiment(
        db_factory=lambda: conn,
        run_id="paired-closed-candle",
        market="KRW-BTC",
        interval="1m",
        now_ms=1_704_046_920_000,
        profile_hash="sha256:profile",
        strategy_parameters_hash="sha256:parameters",
    )

    assert artifact["candle_ts"] == 1_704_046_800_000
    assert artifact["shadow_lane"]["candle_ts"] == artifact["operational_lane"]["candle_ts"]


def test_shadow_lane_does_not_write_live_orders_or_fills(tmp_path: Path) -> None:
    db_path = tmp_path / "paired.sqlite"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE candles(ts INTEGER, pair TEXT, interval TEXT, close REAL)")
    conn.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY)")
    conn.execute(
        "INSERT INTO candles(ts, pair, interval, close) VALUES (?, ?, ?, ?)",
        (1_704_046_800_000, "KRW-BTC", "1m", 100.0),
    )
    before_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    before_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    conn.commit()
    conn.close()

    run_closed_candle_paired_experiment(
        db_factory=lambda: sqlite3.connect(db_path),
        run_id="paired-no-live-writes",
        market="KRW-BTC",
        interval="1m",
        now_ms=1_704_046_920_000,
        profile_hash="sha256:profile",
        strategy_parameters_hash="sha256:parameters",
    )

    verify = sqlite3.connect(db_path)
    try:
        assert verify.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before_orders
        assert verify.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == before_fills
    finally:
        verify.close()


def test_shadow_lane_calls_stage_owned_backtest_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_stage_runner(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            resource_usage={"stage_trace_hash": "sha256:stage-trace"},
            decisions=(),
            execution_event_summary={},
            trades=(),
            final_cash=0.0,
            final_asset_qty=0.0,
        )

    monkeypatch.setattr(paired_experiment, "run_stage_owned_decision_event_backtest", _fake_stage_runner)

    lane = shadow_backtest_lane(_run())

    assert calls
    assert calls[0]["strategy_name"] == "sma_with_filter"
    assert calls[0]["decision_events"]
    assert lane["stage_runner_status"] == "ok"
    assert lane["stages"]["market_input"]["status"] == "ok"


def test_operational_lane_read_only_does_not_submit() -> None:
    submit = Mock()

    artifact = run_paired_experiment(
        _run(submit_enabled=False),
        shadow_lane_runner=_lane,
        operational_lane_runner=operational_runtime_lane,
        broker_submit=submit,
    )

    assert artifact["operational_lane"]["submit_enabled"] is False
    assert artifact["operational_lane"]["read_only"] is True
    assert artifact["operational_lane"]["runtime_path_reason_code"] == "runtime_container_not_injected"
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
        "stage_diffs",
    ):
        assert key in artifact
