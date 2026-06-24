from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from bithumb_bot.decision_equivalence import sha256_prefixed
from bithumb_bot.paired_experiment_diff import PAIRED_EXPERIMENT_STAGE_ORDER, compare_paired_experiment_stages
from bithumb_bot.research.backtest_stage_runner import run_stage_owned_decision_event_backtest
from bithumb_bot.research.dataset_snapshot import Candle, DatasetSnapshot
from bithumb_bot.research.decision_event import ResearchDecisionEvent
from bithumb_bot.research.experiment_manifest import DateRange
from bithumb_bot.runtime.data_cycle_preflight import RuntimeDataCyclePreflightProvider
from bithumb_bot.runtime.decision_coordinator import DecisionCoordinator
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
    market: str = "KRW-BTC"
    interval: str = "1m"
    close: float | None = None

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
        market=snapshot.market,
        interval=snapshot.interval,
        close=snapshot.close,
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
    """Simulated ledger lane backed by the research stage-owned backtest runner."""
    result, status, reason_code = _run_shadow_backtest_stage_runner(run)
    stage_trace = _mapping(getattr(result, "resource_usage", None)).get("stage_trace_hash")
    decisions = getattr(result, "decisions", ()) if result is not None else ()
    decision_payload = dict(decisions[-1]) if decisions else {}
    execution_summary = (
        getattr(result, "execution_event_summary", {})
        if result is not None
        else {}
    )
    stages = _shadow_stage_hashes(
        run,
        status=status,
        reason_code=reason_code,
        stage_trace_hash=None if stage_trace is None else str(stage_trace),
        decision_payload=decision_payload,
        execution_summary=_mapping(execution_summary),
        result=result,
    )
    return {
        "lane": "shadow_backtest",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "simulated_ledger": True,
        "writes_live_db": False,
        "stage_runner": "bithumb_bot.research.backtest_stage_runner.run_stage_owned_decision_event_backtest",
        "stage_runner_status": status,
        "stage_runner_reason_code": reason_code,
        "stages": stages,
    }


def operational_runtime_lane(run: PairedExperimentRun) -> dict[str, object]:
    operational_result = _run_operational_runtime_path(run)
    stages = _operational_stage_hashes(run, operational_result)
    return {
        "lane": "operational_runtime",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "submit_enabled": bool(run.submit_enabled),
        "read_only": not bool(run.submit_enabled),
        "runtime_path": operational_result["runtime_path"],
        "runtime_path_status": operational_result["status"],
        "runtime_path_reason_code": operational_result["reason_code"],
        "stages": stages,
    }


def _stage(stage: str, payload: Mapping[str, object]) -> dict[str, object]:
    stage_payload = {"stage": stage, **dict(payload)}
    return {"status": "ok", "hash": sha256_prefixed(stage_payload), **stage_payload}


def _run_shadow_backtest_stage_runner(run: PairedExperimentRun) -> tuple[object | None, str, str]:
    dataset = _single_candle_dataset(run)
    decision_event = ResearchDecisionEvent(
        candle_ts=int(run.candle_ts),
        decision_ts=int(run.candle_ts),
        strategy_name="sma_with_filter",
        strategy_version="paired_experiment_shadow_v1",
        raw_signal="HOLD",
        final_signal="HOLD",
        reason="paired_experiment_closed_candle_shadow",
        feature_snapshot={"market_snapshot_hash": run.market_snapshot_hash},
        strategy_diagnostics={"paired_experiment_run_id": run.run_id},
        extra_payload={"regime_snapshot": {"composite_regime": "paired_experiment_shadow"}},
    )
    try:
        result = run_stage_owned_decision_event_backtest(
            dataset=dataset,
            strategy_name="sma_with_filter",
            parameter_values={},
            fee_rate=0.0004,
            slippage_bps=0.0,
            decision_events=(decision_event,),
        )
    except Exception as exc:
        return None, "fail_closed", f"shadow_backtest_stage_runner_error:{type(exc).__name__}"
    return result, "ok", "stage_owned_backtest_complete"


def _single_candle_dataset(run: PairedExperimentRun) -> DatasetSnapshot:
    close = float(run.close if run.close is not None else 0.0)
    candle = Candle(
        ts=int(run.candle_ts),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
    )
    day = datetime.fromtimestamp(int(run.candle_ts) / 1000, tz=timezone.utc).date().isoformat()
    return DatasetSnapshot(
        snapshot_id=f"paired-experiment:{run.run_id}",
        source="paired_experiment_closed_live_candle",
        market=str(run.market),
        interval=str(run.interval),
        split_name="paired_closed_candle",
        date_range=DateRange(start=day, end=day),
        candles=(candle,),
        source_content_hash=run.market_snapshot_hash,
        locator={"run_id": run.run_id, "candle_ts": int(run.candle_ts)},
    )


def _shadow_stage_hashes(
    run: PairedExperimentRun,
    *,
    status: str,
    reason_code: str,
    stage_trace_hash: str | None,
    decision_payload: Mapping[str, object],
    execution_summary: Mapping[str, object],
    result: object | None,
) -> dict[str, dict[str, object]]:
    common = {
        "lane": "shadow",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "stage_runner_status": status,
        "stage_runner_reason_code": reason_code,
        "stage_trace_hash": stage_trace_hash,
    }
    stages = {
        "market_input": _stage_with_status(
            "market_input",
            {**common, "market_snapshot_hash": run.market_snapshot_hash},
            status,
            reason_code,
        ),
        "feature_projection": _stage_with_status(
            "feature_projection",
            {**common, "decision_payload_hash": sha256_prefixed(dict(decision_payload))},
            status,
            reason_code,
        ),
        "position_snapshot": _stage_with_status(
            "position_snapshot",
            {
                **common,
                "ledger": "simulated_backtest",
                "shadow_initial_state_hash": run.shadow_initial_state_hash,
            },
            status,
            reason_code,
        ),
        "daily_count_snapshot": _stage_with_status(
            "daily_count_snapshot",
            {**common, "source": "simulated_ledger"},
            status,
            reason_code,
        ),
        "strategy_decision": _stage_with_status(
            "strategy_decision",
            {**common, "decision_payload_hash": sha256_prefixed(dict(decision_payload))},
            status,
            reason_code,
        ),
        "risk_decision": _stage_with_status(
            "risk_decision",
            {**common, "decision_payload_hash": sha256_prefixed(dict(decision_payload))},
            status,
            reason_code,
        ),
        "portfolio_target": _stage_with_status(
            "portfolio_target",
            {
                **common,
                "final_cash": getattr(result, "final_cash", None),
                "final_asset_qty": getattr(result, "final_asset_qty", None),
            },
            status,
            reason_code,
        ),
        "execution_plan": _stage_with_status(
            "execution_plan",
            {**common, "execution_summary_hash": sha256_prefixed(dict(execution_summary))},
            status,
            reason_code,
        ),
        "submit_authority": _stage_with_status(
            "submit_authority",
            {**common, "simulated_submit_authority": True},
            status,
            reason_code,
        ),
        "broker_payload": _stage_with_status(
            "broker_payload",
            {**common, "simulated_broker_payload": True},
            status,
            reason_code,
        ),
        "fill": _stage_with_status(
            "fill",
            {
                **common,
                "simulated": True,
                "execution_summary_hash": sha256_prefixed(dict(execution_summary)),
            },
            status,
            reason_code,
        ),
        "accounting": _stage_with_status(
            "accounting",
            {
                **common,
                "ledger": "simulated",
                "trades_hash": sha256_prefixed(list(getattr(result, "trades", ()) or ())),
            },
            status,
            reason_code,
        ),
    }
    return stages


def _run_operational_runtime_path(run: PairedExperimentRun) -> dict[str, object]:
    result = {
        "runtime_path": (
            "RuntimeDataCyclePreflightProvider.evaluate"
            " -> DecisionCoordinator.decide_cycle"
            " -> run_loop_execution_planner"
        ),
        "status": "fail_closed",
        "reason_code": "runtime_container_not_injected",
        "preflight_hash": None,
        "decision_hash": None,
        "execution_plan_bundle_hash": None,
    }
    container = getattr(run, "runtime_container", None)
    strategy_set = getattr(run, "runtime_strategy_set", None)
    runtime_checkpoint = getattr(run, "runtime_checkpoint", None)
    runtime_events = getattr(run, "runtime_events", None)
    if container is None or strategy_set is None or runtime_checkpoint is None or runtime_events is None:
        return result
    try:
        preflight = RuntimeDataCyclePreflightProvider(
            container=container,
            runtime_checkpoint=runtime_checkpoint,
            runtime_events=runtime_events,
        ).evaluate(
            strategy_set=strategy_set,
            now_epoch_sec=float(container.clock()),
            interval_sec=parse_interval_sec(str(run.interval)),
        )
        preflight_payload = preflight.as_dict()
        decision = DecisionCoordinator(
            settings_obj=container.settings_obj,
            db_factory=container.db_factory,
            broker_provider=lambda: None,
        ).decide_cycle(
            runtime_strategy_set=strategy_set,
            candle_ts=int(run.candle_ts),
            updated_ts=int(float(container.clock()) * 1000),
            runtime_data_cycle_preflight_hash=str(preflight_payload["decision_hash"]),
            runtime_data_availability_report_hash=preflight.runtime_data_availability_report_hash,
            broker=None,
        )
    except Exception as exc:
        result["reason_code"] = f"operational_runtime_path_error:{type(exc).__name__}"
        return result
    result.update(
        {
            "status": "ok",
            "reason_code": "operational_runtime_path_complete",
            "preflight_hash": preflight_payload["decision_hash"],
            "decision_hash": decision.as_dict()["decision_hash"],
            "execution_plan_bundle_hash": decision.execution_plan_bundle_hash,
            "decision_result": decision.as_dict(),
        }
    )
    return result


def _operational_stage_hashes(
    run: PairedExperimentRun,
    operational_result: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    status = str(operational_result.get("status") or "fail_closed")
    reason_code = str(operational_result.get("reason_code") or "operational_runtime_path_unavailable")
    common = {
        "lane": "operational",
        "run_id": run.run_id,
        "candle_ts": run.candle_ts,
        "submit_enabled": bool(run.submit_enabled),
        "runtime_path_status": status,
        "runtime_path_reason_code": reason_code,
        "runtime_data_cycle_preflight_hash": operational_result.get("preflight_hash"),
        "decision_hash": operational_result.get("decision_hash"),
        "execution_plan_bundle_hash": operational_result.get("execution_plan_bundle_hash"),
    }
    stages = {
        stage: _stage_with_status(stage, common, status, reason_code)
        for stage in PAIRED_EXPERIMENT_STAGE_ORDER
    }
    stages["market_input"] = _stage_with_status(
        "market_input",
        {**common, "market_snapshot_hash": run.market_snapshot_hash},
        status,
        reason_code,
    )
    stages["position_snapshot"] = _stage_with_status(
        "position_snapshot",
        {**common, "actual_state_snapshot_hash": run.actual_state_snapshot_hash},
        status,
        reason_code,
    )
    stages["submit_authority"] = _stage_with_status(
        "submit_authority",
        {
            **common,
            "mode": "submit_capable" if run.submit_enabled else "read_only_no_submit",
            "submit_enabled": bool(run.submit_enabled),
        },
        status,
        reason_code,
    )
    return stages


def _stage_with_status(
    stage: str,
    payload: Mapping[str, object],
    status: str,
    reason_code: str,
) -> dict[str, object]:
    stage_payload = {"stage": stage, **dict(payload)}
    return {
        "status": status,
        "reason_code": "" if status == "ok" else reason_code,
        "hash": sha256_prefixed(stage_payload),
        **stage_payload,
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


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
