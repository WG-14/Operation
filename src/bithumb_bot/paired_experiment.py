from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Callable, Mapping

from bithumb_bot.decision_equivalence import sha256_prefixed
from bithumb_bot.paired_experiment_diff import PAIRED_EXPERIMENT_STAGE_ORDER, compare_paired_experiment_stages
from bithumb_bot.runtime_data_access import select_latest_closed_candle
from bithumb_bot.utils_time import parse_interval_sec


LaneRunner = Callable[["PairedExperimentRun"], Mapping[str, object]]
BrokerSubmit = Callable[..., object]


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


@dataclass(frozen=True)
class ClosedCandleSnapshot:
    candle_ts: int
    close: float | None
    market: str
    interval: str
    market_snapshot_hash: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def select_closed_candle_snapshot(
    conn: sqlite3.Connection,
    *,
    market: str,
    interval: str,
    now_ms: int,
) -> ClosedCandleSnapshot:
    row, _incomplete_ts = select_latest_closed_candle(
        conn,
        pair=market,
        interval=interval,
        interval_sec=parse_interval_sec(interval),
        now_ms=int(now_ms),
        is_closed_candle=_is_closed_candle,
    )
    if row is None:
        raise ValueError("paired_experiment_closed_candle_snapshot_missing")
    candle_ts = _row_int(row, "ts", 0)
    close = _row_float(row, "close", 1)
    payload = {
        "market": market,
        "interval": interval,
        "candle_ts": candle_ts,
        "close": close,
    }
    return ClosedCandleSnapshot(
        candle_ts=candle_ts,
        close=close,
        market=market,
        interval=interval,
        market_snapshot_hash=sha256_prefixed(payload),
    )


def make_paired_experiment_run(
    *,
    run_id: str,
    snapshot: ClosedCandleSnapshot,
    profile_hash: str,
    strategy_parameters_hash: str,
    actual_state_snapshot: Mapping[str, object] | None = None,
    shadow_initial_state: Mapping[str, object] | None = None,
    submit_enabled: bool = False,
) -> PairedExperimentRun:
    return PairedExperimentRun(
        run_id=run_id,
        candle_ts=snapshot.candle_ts,
        market_snapshot_hash=snapshot.market_snapshot_hash,
        profile_hash=profile_hash,
        strategy_parameters_hash=strategy_parameters_hash,
        shadow_initial_state_hash=sha256_prefixed(dict(shadow_initial_state or {"ledger": "simulated"})),
        actual_state_snapshot_hash=sha256_prefixed(dict(actual_state_snapshot or {"state": "runtime_read_only"})),
        submit_enabled=submit_enabled,
    )


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


def run_closed_candle_paired_experiment(
    *,
    db_factory: Callable[[], sqlite3.Connection],
    run_id: str,
    market: str,
    interval: str,
    now_ms: int,
    profile_hash: str,
    strategy_parameters_hash: str,
    submit_enabled: bool = False,
    broker_submit: BrokerSubmit | None = None,
) -> dict[str, object]:
    conn = db_factory()
    try:
        snapshot = select_closed_candle_snapshot(conn, market=market, interval=interval, now_ms=now_ms)
        actual_state = _actual_state_snapshot(conn, candle_ts=snapshot.candle_ts)
    finally:
        conn.close()
    run = make_paired_experiment_run(
        run_id=run_id,
        snapshot=snapshot,
        profile_hash=profile_hash,
        strategy_parameters_hash=strategy_parameters_hash,
        actual_state_snapshot=actual_state,
        shadow_initial_state={"ledger": "simulated", "candle_ts": snapshot.candle_ts},
        submit_enabled=submit_enabled,
    )
    return run_paired_experiment(
        run,
        shadow_lane_runner=shadow_backtest_lane,
        operational_lane_runner=operational_runtime_lane,
        broker_submit=broker_submit,
    )


def shadow_backtest_lane(run: PairedExperimentRun) -> dict[str, object]:
    """Simulated ledger lane: no runtime DB connection or live table writes."""
    stages = {
        stage: _stage(stage, {"lane": "shadow", "run_id": run.run_id, "candle_ts": run.candle_ts})
        for stage in PAIRED_EXPERIMENT_STAGE_ORDER
    }
    stages["position_snapshot"] = _stage(
        "position_snapshot",
        {
            "lane": "shadow",
            "ledger": "simulated_backtest",
            "shadow_initial_state_hash": run.shadow_initial_state_hash,
        },
    )
    stages["fill"] = _stage("fill", {"lane": "shadow", "simulated": True})
    stages["accounting"] = _stage("accounting", {"lane": "shadow", "ledger": "simulated"})
    return {
        "lane": "shadow_backtest",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "simulated_ledger": True,
        "writes_live_db": False,
        "stages": stages,
    }


def operational_runtime_lane(run: PairedExperimentRun) -> dict[str, object]:
    stages = {
        stage: _stage(
            stage,
            {
                "lane": "operational",
                "run_id": run.run_id,
                "candle_ts": run.candle_ts,
                "submit_enabled": bool(run.submit_enabled),
            },
        )
        for stage in PAIRED_EXPERIMENT_STAGE_ORDER
    }
    stages["position_snapshot"] = _stage(
        "position_snapshot",
        {
            "lane": "operational",
            "actual_state_snapshot_hash": run.actual_state_snapshot_hash,
        },
    )
    stages["submit_authority"] = _stage(
        "submit_authority",
        {
            "lane": "operational",
            "mode": "submit_capable" if run.submit_enabled else "read_only_no_submit",
            "submit_enabled": bool(run.submit_enabled),
        },
    )
    return {
        "lane": "operational_runtime",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "submit_enabled": bool(run.submit_enabled),
        "read_only": not bool(run.submit_enabled),
        "stages": stages,
    }


def _stage(stage: str, payload: Mapping[str, object]) -> dict[str, object]:
    stage_payload = {"stage": stage, **dict(payload)}
    return {"status": "ok", "hash": sha256_prefixed(stage_payload), **stage_payload}


def _actual_state_snapshot(conn: sqlite3.Connection, *, candle_ts: int) -> dict[str, object]:
    return {
        "candle_ts": int(candle_ts),
        "orders_count": _count_table(conn, "orders"),
        "fills_count": _count_table(conn, "fills"),
        "portfolio_count": _count_table(conn, "portfolio"),
        "target_position_state_count": _count_table(conn, "target_position_state"),
    }


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row is not None else 0


def _is_closed_candle(*, candle_ts_ms: int, now_ms: int, interval_sec: int) -> bool:
    return int(candle_ts_ms) + int(interval_sec) * 1000 <= int(now_ms)


def _row_int(row: object, key: str, index: int) -> int:
    return int(row[key] if hasattr(row, "keys") else row[index])


def _row_float(row: object, key: str, index: int) -> float | None:
    value = row[key] if hasattr(row, "keys") else row[index]
    return None if value is None else float(value)


__all__ = [
    "ClosedCandleSnapshot",
    "PairedExperimentRun",
    "make_paired_experiment_run",
    "operational_runtime_lane",
    "run_closed_candle_paired_experiment",
    "run_paired_experiment",
    "select_closed_candle_snapshot",
    "shadow_backtest_lane",
]
