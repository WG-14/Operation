from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .storage_io import write_json_atomic


class H74StateCleanupError(RuntimeError):
    pass


PROTECTED_TABLES = ("trade_lifecycles", "orders", "fills", "trades")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[object, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return int(conn.execute(sql, params).fetchone()[0])


def _portfolio_asset_qty(conn: sqlite3.Connection) -> float:
    if not _table_exists(conn, "portfolio"):
        return 0.0
    row = conn.execute("SELECT asset_qty FROM portfolio WHERE id=1").fetchone()
    return 0.0 if row is None else float(row[0] or 0.0)


def h74_non_authoritative_state_summary(conn: sqlite3.Connection, *, pair: str) -> dict[str, object]:
    return {
        "pair": pair,
        "portfolio_asset_qty": _portfolio_asset_qty(conn),
        "open_position_lot_count": _count(conn, "open_position_lots", "pair=? AND qty_open > 1e-12", (pair,)),
        "risky_order_count": _count(
            conn,
            "orders",
            "pair=? AND lower(status) IN ('open','submitted','partially_filled','pending','submit_unknown','recovery_required')",
            (pair,),
        ),
        "target_position_state_count": _count(conn, "target_position_state", "pair=?", (pair,)),
        "h74_virtual_target_state_count": _count(
            conn,
            "strategy_virtual_target_state",
            "pair=? AND (strategy_name='daily_participation_sma' OR strategy_instance_id LIKE 'daily_participation_sma:%' OR strategy_instance_id LIKE 'h74%')",
            (pair,),
        ),
        "protected_counts": {table: _count(conn, table) for table in PROTECTED_TABLES},
    }


def clear_h74_non_authoritative_state(
    conn: sqlite3.Connection,
    *,
    pair: str,
    backup_path: str | Path,
    require_flat: bool = True,
    broker_convergence_ok: bool = False,
    allow_broker_unverified: bool = False,
) -> dict[str, object]:
    if not str(pair or "").strip():
        raise H74StateCleanupError("h74_state_cleanup_pair_required")
    before = h74_non_authoritative_state_summary(conn, pair=pair)
    if require_flat and abs(float(before["portfolio_asset_qty"])) > 1e-12:
        raise H74StateCleanupError("h74_state_cleanup_refused_portfolio_asset_qty_nonzero")
    if int(before["open_position_lot_count"]) > 0:
        raise H74StateCleanupError("h74_state_cleanup_refused_open_lot_exists")
    if int(before["risky_order_count"]) > 0:
        raise H74StateCleanupError("h74_state_cleanup_refused_risky_order_exists")
    if not broker_convergence_ok and not allow_broker_unverified:
        raise H74StateCleanupError("h74_state_cleanup_refused_broker_unverified")
    backup_payload = {
        "artifact_type": "h74_non_authoritative_state_cleanup_backup",
        "created_ts": int(time.time()),
        "before": before,
        "rows": {
            "target_position_state": [
                dict(row)
                for row in conn.execute("SELECT * FROM target_position_state WHERE pair=?", (pair,))
            ]
            if _table_exists(conn, "target_position_state")
            else [],
            "strategy_virtual_target_state": [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM strategy_virtual_target_state
                    WHERE pair=?
                      AND (strategy_name='daily_participation_sma'
                           OR strategy_instance_id LIKE 'daily_participation_sma:%'
                           OR strategy_instance_id LIKE 'h74%')
                    """,
                    (pair,),
                )
            ]
            if _table_exists(conn, "strategy_virtual_target_state")
            else [],
        },
    }
    write_json_atomic(Path(backup_path), backup_payload)
    protected_before = dict(before["protected_counts"])
    conn.execute("DELETE FROM target_position_state WHERE pair=?", (pair,))
    conn.execute(
        """
        DELETE FROM strategy_virtual_target_state
        WHERE pair=?
          AND (strategy_name='daily_participation_sma'
               OR strategy_instance_id LIKE 'daily_participation_sma:%'
               OR strategy_instance_id LIKE 'h74%')
        """,
        (pair,),
    )
    after = h74_non_authoritative_state_summary(conn, pair=pair)
    if dict(after["protected_counts"]) != protected_before:
        raise H74StateCleanupError("h74_state_cleanup_protected_table_count_changed")
    summary = {
        "artifact_type": "h74_non_authoritative_state_cleanup_summary",
        "status": "deleted",
        "backup_path": str(backup_path),
        "before": before,
        "after": after,
    }
    return summary
