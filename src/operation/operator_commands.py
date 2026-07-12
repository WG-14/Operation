"""Exchange-neutral, SQLite-backed operator commands.

These commands deliberately do not construct a broker for local inspection.
Live broker work belongs behind the availability boundary and is not exposed
while no adapter is installed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import PATH_MANAGER, config_contract_metadata, settings, validate_live_mode_preflight
from .db_core import compute_accounting_replay, diagnose_db_path, ensure_db, get_portfolio_breakdown, init_portfolio
from .run_lock import acquire_run_lock, read_run_lock_status
from .storage_io import write_json_atomic


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _row_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    return dict(row) if row is not None else None


def _query_one(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
    try:
        return conn.execute(query, params).fetchone()
    except sqlite3.OperationalError:
        return None


def _query_rows(conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    try:
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def _local_state() -> dict[str, object]:
    """Read local runtime state without a broker dependency."""
    conn = ensure_db()
    try:
        init_portfolio(conn)
        health = _row_dict(
            _query_one(
                conn,
                """SELECT last_candle_ts_ms, last_candle_age_sec, error_count, trading_enabled,
                          last_disable_reason, halt_reason_code, halt_state_unresolved,
                          unresolved_open_order_count, recovery_required_count
                   FROM bot_health WHERE id=1""",
            )
        ) or {}
        candle = _row_dict(
            _query_one(conn, "SELECT ts, close FROM candles WHERE market=? ORDER BY ts DESC LIMIT 1", (settings.PAIR,))
        ) or {}
        counts = _row_dict(
            _query_one(
                conn,
                """SELECT
                     SUM(CASE WHEN status IN ('PENDING_SUBMIT','NEW','PARTIAL','ACCOUNTING_PENDING','CANCEL_REQUESTED','SUBMIT_UNKNOWN') THEN 1 ELSE 0 END) AS open_order_count,
                     SUM(CASE WHEN status='RECOVERY_REQUIRED' THEN 1 ELSE 0 END) AS recovery_required_count
                   FROM orders""",
            )
        ) or {}
        cash_available, cash_locked, asset_available, asset_locked = get_portfolio_breakdown(conn)
    finally:
        conn.close()
    lock = read_run_lock_status(PATH_MANAGER.run_lock_path()).as_dict()
    return {
        "mode": settings.MODE,
        "market": settings.PAIR,
        "interval": settings.INTERVAL,
        "db_path": settings.DB_PATH,
        "latest_candle": candle or None,
        "runtime_health": health or None,
        "open_order_count": int(counts.get("open_order_count") or 0),
        "recovery_required_count": int(
            counts.get("recovery_required_count") or health.get("recovery_required_count") or 0
        ),
        "position": {
            "cash_available": float(cash_available), "cash_locked": float(cash_locked),
            "asset_available": float(asset_available), "asset_locked": float(asset_locked),
        },
        "run_lock": lock,
        "broker_status": "not_configured",
        "live_capability": "blocked",
    }


def cmd_config_dump(*, masked: bool = False) -> None:
    metadata = config_contract_metadata(settings)
    payload = {
        "mode": settings.MODE,
        "pair": settings.PAIR,
        "interval": settings.INTERVAL,
        "db_path": settings.DB_PATH,
        "broker_status": "not_configured",
        "live_capability": "blocked",
        "config_contract": metadata,
    }
    if masked:
        payload["masked"] = True
    _json(payload)


def cmd_pause() -> None:
    from .runtime_state import disable_trading_until

    disable_trading_until(float("inf"), reason="operator pause")
    print("[PAUSE] trading paused")


def cmd_status() -> None:
    _json(_local_state())


def cmd_health() -> int:
    try:
        state = _local_state()
    except Exception as exc:
        _json({"ok": False, "health": "db_unavailable", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    health = state.get("runtime_health") if isinstance(state.get("runtime_health"), dict) else {}
    candle = state.get("latest_candle") if isinstance(state.get("latest_candle"), dict) else {}
    recovery_required = int(state.get("recovery_required_count") or 0)
    unresolved = int(state.get("open_order_count") or 0)
    error_count = int(health.get("error_count") or 0)
    max_errors = int(getattr(settings, "HEALTH_MAX_ERROR_COUNT", 0))
    max_age = float(getattr(settings, "HEALTH_MAX_CANDLE_AGE_SEC", 0.0))
    candle_age = health.get("last_candle_age_sec")
    stale = bool(candle_age is not None and max_age > 0 and float(candle_age) > max_age)
    ok = not (recovery_required or unresolved or stale or (max_errors >= 0 and error_count > max_errors))
    payload = {
        "ok": ok,
        "paper_runtime_status": "local_state_available" if candle else "no_candles",
        "latest_candle": candle or None,
        "candle_stale": stale,
        "runtime_error_count": error_count,
        "trading_enabled": health.get("trading_enabled"),
        "halt_reason": health.get("halt_reason_code"),
        "unresolved_order_count": unresolved,
        "recovery_required_count": recovery_required,
        "run_lock": state["run_lock"],
        "broker_status": "not_configured",
        "live_capability": "blocked",
    }
    _json(payload)
    return 0 if ok else 1


def cmd_validate_db(*, as_json: bool = False) -> int:
    try:
        diagnostic = diagnose_db_path(settings.DB_PATH)
        conn = ensure_db()
        try:
            conn.execute("PRAGMA schema_version").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _json(payload) if as_json else print(f"[VALIDATE-DB] failed: {payload['error']}")
        return 1
    payload = {"ok": True, "db": diagnostic, "mode": settings.MODE}
    if as_json:
        _json(payload)
        return 0

    print(f"[VALIDATE-DB] ok db_path={settings.DB_PATH}")
    for key in (
        "status",
        "expected_schema_version",
        "observed_schema_version",
        "expected_accounting_projection_model",
        "observed_accounting_projection_model",
        "diagnostic_schema_status",
        "diagnostic_recommended_command",
    ):
        if key not in diagnostic:
            continue
        label = "db_schema_status" if key == "status" else key
        print(f"{label}={diagnostic[key]}")
    for table in diagnostic.get("diagnostic_missing_tables", []):
        print(f"diagnostic_schema_warning=missing table: {table}")
    for table, columns in dict(diagnostic.get("diagnostic_missing_columns", {})).items():
        for column in columns:
            print(f"diagnostic_schema_warning=missing column: {table}.{column}")
    return 0


def cmd_orders(limit: int = 50) -> None:
    conn = ensure_db()
    try:
        rows = _query_rows(conn, "SELECT * FROM orders ORDER BY ts_ms DESC LIMIT ?", (max(1, int(limit)),))
    finally:
        conn.close()
    _json({"orders": rows, "limit": max(1, int(limit))})


def cmd_fills(limit: int = 50) -> None:
    conn = ensure_db()
    try:
        rows = _query_rows(conn, "SELECT * FROM fills ORDER BY fill_ts DESC LIMIT ?", (max(1, int(limit)),))
    finally:
        conn.close()
    _json({"fills": rows, "limit": max(1, int(limit))})


def cmd_trades(limit: int = 20) -> None:
    conn = ensure_db()
    try:
        rows = _query_rows(conn, "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))
    finally:
        conn.close()
    _json({"trades": rows, "limit": max(1, int(limit))})


def cmd_audit() -> int:
    conn = ensure_db()
    try:
        replay = compute_accounting_replay(conn)
    finally:
        conn.close()
    ok = bool(replay.get("consistent", False))
    _json({"ok": ok, "accounting_replay": replay})
    return 0 if ok else 1


def cmd_audit_ledger() -> int:
    return cmd_audit()


def cmd_signal(short: int, long: int) -> None:
    state = _local_state()
    candle = state.get("latest_candle")
    if not candle:
        _json({"signal": "HOLD", "reason": "no_candles_after_sync", "short": short, "long": long})
        return
    _json({"signal": "unavailable", "reason": "use_run_for_persisted_strategy_decision", "short": short, "long": long, "latest_candle": candle})


def cmd_explain(short: int, long: int) -> None:
    cmd_signal(short, long)


def cmd_run() -> int:
    if settings.MODE == "live":
        try:
            validate_live_mode_preflight(settings)
        except Exception as exc:
            print(f"LIVE_BROKER_NOT_CONFIGURED: {exc}")
            return 1
        return 1
    from .runtime.app_container import create_default_runtime_app

    with acquire_run_lock(PATH_MANAGER.run_lock_path()):
        artifact = create_default_runtime_app(settings).runner.run_one_cycle()
    _json({"mode": "paper", "cycle": artifact.as_dict() if artifact is not None else {"status": "blocked_or_no_data"}})
    return 0


def cmd_report(days: int) -> None:
    from .reporting import cmd_ops_report

    cmd_ops_report(limit=max(1, int(days)))
    cmd_execution_quality_report(
        limit=max(1, int(days)),
        since=None,
        market=None,
        mode=None,
        compare_approval=None,
        output_format="text",
        group_by=None,
        write_calibration=False,
    )


def cmd_execution_quality_report(**kwargs: object) -> None:
    from .execution_calibration import build_calibration_artifact, write_calibration_artifact
    from .execution_quality import ExecutionQualityThresholds, format_execution_quality_text, refresh_execution_quality_records, summarize_execution_quality

    limit = max(1, int(kwargs.get("limit", 200)))
    conn = ensure_db()
    try:
        records = refresh_execution_quality_records(conn, limit=limit, market=kwargs.get("market"), mode=kwargs.get("mode"))
        fill_count_row = conn.execute(
            "SELECT COUNT(*) AS fill_count, AVG(slippage_bps) AS avg_slippage_bps FROM fills"
        ).fetchone()
        fill_count = int(fill_count_row["fill_count"] if fill_count_row is not None else 0)
        avg_slippage_bps = (
            float(fill_count_row["avg_slippage_bps"])
            if fill_count_row is not None and fill_count_row["avg_slippage_bps"] is not None
            else None
        )
        conn.commit()
    finally:
        conn.close()
    thresholds = ExecutionQualityThresholds(min_sample=max(1, int(settings.LIVE_EXECUTION_QUALITY_MIN_SAMPLE)), max_p90_slippage_bps=float(settings.LIVE_EXECUTION_QUALITY_MAX_P90_SLIPPAGE_BPS), max_p95_full_fill_latency_ms=float(settings.LIVE_EXECUTION_QUALITY_MAX_P95_FULL_FILL_LATENCY_MS), max_partial_fill_rate=float(settings.LIVE_EXECUTION_QUALITY_MAX_PARTIAL_FILL_RATE), max_model_breach_rate=float(settings.LIVE_EXECUTION_QUALITY_MAX_MODEL_BREACH_RATE))
    summary = summarize_execution_quality(records, thresholds=thresholds)
    if bool(kwargs.get("write_calibration")):
        artifact = build_calibration_artifact(summary=summary, market=str(kwargs.get("market") or settings.PAIR), interval=settings.INTERVAL)
        summary["calibration_path"] = str(write_calibration_artifact(manager=PATH_MANAGER, artifact=artifact))
    if kwargs.get("output_format") == "json":
        _json(summary)
    else:
        measured = sum(1 for record in records if getattr(record, "slippage_vs_submit_ref_bps", None) is not None)
        print(f"[EXECUTION-QUALITY] fills={fill_count} measured={measured}")
        if avg_slippage_bps is not None:
            print(f"avg_slippage_bps={avg_slippage_bps:.3f}")
        print(format_execution_quality_text(summary))


def _recovery_snapshot() -> dict[str, object]:
    state = _local_state()
    return {
        **state,
        "generated_at_epoch_sec": time.time(),
        "recovery_stage": "operator_review_required" if state["recovery_required_count"] else "clear",
        "broker_observation": "not_configured",
    }


def cmd_recovery_report(*, as_json: bool = False) -> None:
    report = _recovery_snapshot()
    write_json_atomic(PATH_MANAGER.recovery_report_path(), report)
    _json(report) if as_json else print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def cmd_repair_plan(*, as_json: bool = False) -> None:
    snapshot = _recovery_snapshot()
    plan = {"read_only": True, "recovery_stage": snapshot["recovery_stage"], "recommended_action": "review_recovery_report" if snapshot["recovery_required_count"] else "no_repair_required", "local_state": snapshot}
    _json(plan) if as_json else print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def cmd_restart_checklist() -> None:
    snapshot = _recovery_snapshot()
    blocked = bool(snapshot["open_order_count"] or snapshot["recovery_required_count"])
    _json({"safe_to_resume": not blocked, "local_state": snapshot})


def cmd_residual_closeout_plan(*, as_json: bool = False) -> None:
    state = _local_state()
    payload = {"read_only": True, "broker_observation": "not_configured", "position": state["position"], "recommended_action": "operator_review"}
    _json(payload) if as_json else print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_diagnose_fill_trade_linkage(*, as_json: bool = False, apply_safe: bool = False) -> None:
    conn = ensure_db()
    try:
        rows = _query_rows(conn, "SELECT * FROM fills WHERE trade_id IS NULL ORDER BY fill_ts DESC")
    finally:
        conn.close()
    payload = {"unlinked_fill_count": len(rows), "fills": rows, "apply_safe": apply_safe, "mutation": "not_applied_without_unambiguous_linkage_service"}
    _json(payload) if as_json else print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _require_apply(apply: bool, confirm: bool) -> bool:
    return bool(apply and confirm)


def cmd_fee_gap_accounting_repair(*, apply: bool = False, confirm: bool = False, note: str | None = None) -> None:
    from .fee_gap_repair import apply_fee_gap_accounting_repair, build_fee_gap_accounting_repair_preview
    conn = ensure_db()
    try:
        payload = apply_fee_gap_accounting_repair(conn, note=note) if _require_apply(apply, confirm) else build_fee_gap_accounting_repair_preview(conn)
        conn.commit()
    finally:
        conn.close()
    _json(payload)


def cmd_fee_pending_accounting_repair(*, client_order_id: str, fill_id: str | None, exchange_order_id: str | None, fee: float | None, fee_provenance: str | None, apply: bool, confirm: bool, note: str | None) -> None:
    from .fee_pending_repair import apply_fee_pending_accounting_repair, build_fee_pending_accounting_repair_preview
    conn = ensure_db()
    try:
        kwargs = dict(client_order_id=client_order_id, fill_id=fill_id, exchange_order_id=exchange_order_id, fee=fee, fee_provenance=fee_provenance)
        payload = apply_fee_pending_accounting_repair(conn, note=note, **kwargs) if _require_apply(apply, confirm) else build_fee_pending_accounting_repair_preview(conn, **kwargs)
        conn.commit()
    finally:
        conn.close()
    _json(payload)


def cmd_rebuild_position_authority(*, apply: bool, confirm: bool, note: str | None, **kwargs: object) -> None:
    from .position_authority_repair import apply_position_authority_rebuild, build_position_authority_rebuild_preview
    conn = ensure_db()
    try:
        payload = apply_position_authority_rebuild(conn, note=note) if _require_apply(apply, confirm) else build_position_authority_rebuild_preview(conn)
        conn.commit()
    finally:
        conn.close()
    _json(payload)


def cmd_record_external_cash_adjustment(**kwargs: object) -> None:
    from .db_core import record_external_cash_adjustment
    if not bool(kwargs.get("yes")):
        _json({"applied": False, "reason": "--yes is required"})
        return
    conn = ensure_db()
    try:
        payload = record_external_cash_adjustment(conn, **{key: value for key, value in kwargs.items() if key != "yes"})
        conn.commit()
    finally:
        conn.close()
    _json(payload)


def _simple_repair(module: str, preview_name: str, apply_name: str, *, apply: bool, confirm: bool, note: str | None) -> None:
    mod = __import__(f"operation.{module}", fromlist=[preview_name, apply_name])
    conn = ensure_db()
    try:
        payload = getattr(mod, apply_name)(conn, note=note) if _require_apply(apply, confirm) else getattr(mod, preview_name)(conn)
        conn.commit()
    finally:
        conn.close()
    _json(payload)


def cmd_manual_flat_accounting_repair(*, apply: bool = False, confirm: bool = False, note: str | None = None) -> None:
    _simple_repair("manual_flat_repair", "build_manual_flat_accounting_repair_preview", "apply_manual_flat_accounting_repair", apply=apply, confirm=confirm, note=note)


def cmd_external_position_accounting_repair(*, apply: bool = False, confirm: bool = False, note: str | None = None) -> None:
    _simple_repair("external_position_repair", "build_external_position_accounting_repair_preview", "apply_external_position_accounting_repair", apply=apply, confirm=confirm, note=note)
