from __future__ import annotations

import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _first_id(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[object, ...] = ()) -> object | None:
    if not _table_exists(conn, table):
        return None
    sql = f"SELECT id FROM {table}" + (f" WHERE {where}" if where else "") + " ORDER BY id LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[object, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return int(conn.execute(sql, params).fetchone()[0])


def _plan_submit_count(conn: sqlite3.Connection, side: str) -> int:
    if not _table_exists(conn, "execution_plan"):
        return 0
    cols = _columns(conn, "execution_plan")
    side_clause = "upper(side)=?" if "side" in cols else "1=1"
    submit_clause = "submit_expected=1" if "submit_expected" in cols else "1=1"
    return _count(conn, "execution_plan", f"{side_clause} AND {submit_clause}", (side,) if "side" in cols else ())


def _order_event_count(conn: sqlite3.Connection, side: str) -> int:
    if not _table_exists(conn, "order_events"):
        return 0
    cols = _columns(conn, "order_events")
    side_clause = "upper(side)=?" if "side" in cols else "1=1"
    kind_terms = []
    if "event_type" in cols:
        kind_terms.append("lower(event_type) LIKE '%submit%'")
    if "event_kind" in cols:
        kind_terms.append("lower(event_kind) LIKE '%submit%'")
    kind_clause = "(" + " OR ".join(kind_terms) + ")" if kind_terms else "1=1"
    exception_clause = "AND (exception_class IS NULL OR exception_class='')" if "exception_class" in cols else ""
    return _count(conn, "order_events", f"{side_clause} AND {kind_clause} {exception_clause}", (side,) if "side" in cols else ())


def _order_id_for_side(conn: sqlite3.Connection, side: str) -> object | None:
    if not _table_exists(conn, "orders"):
        return None
    return _first_id(conn, "orders", "upper(side)=?", (side,))


def _fill_id_for_side(conn: sqlite3.Connection, side: str) -> object | None:
    if not _table_exists(conn, "fills") or not _table_exists(conn, "orders"):
        return None
    row = conn.execute(
        """
        SELECT f.id
        FROM fills f
        JOIN orders o ON o.client_order_id=f.client_order_id
        WHERE upper(o.side)=?
        ORDER BY f.id LIMIT 1
        """,
        (side,),
    ).fetchone()
    return None if row is None else row[0]


def generate_h74_execution_path_probe_report(
    conn: sqlite3.Connection,
    *,
    pair: str = "KRW-BTC",
    min_executable_qty: float = 0.0,
) -> dict[str, object]:
    buy_decision_id = _first_id(conn, "strategy_decisions", "upper(signal)='BUY'")
    sell_decision_id = _first_id(conn, "strategy_decisions", "upper(signal)='SELL'")
    buy_plan_ok = _plan_submit_count(conn, "BUY") > 0
    sell_plan_ok = _plan_submit_count(conn, "SELL") > 0
    buy_order_id = _order_id_for_side(conn, "BUY")
    sell_order_id = _order_id_for_side(conn, "SELL")
    buy_fill_id = _fill_id_for_side(conn, "BUY")
    sell_fill_id = _fill_id_for_side(conn, "SELL")
    buy_event_ok = _order_event_count(conn, "BUY") > 0
    sell_event_ok = _order_event_count(conn, "SELL") > 0
    open_lot_created = _count(conn, "open_position_lots", "pair=?", (pair,)) > 0
    lifecycle_id = _first_id(conn, "trade_lifecycles", "pair=?", (pair,))
    final_qty = 0.0
    if _table_exists(conn, "portfolio"):
        row = conn.execute("SELECT asset_qty FROM portfolio WHERE id=1").fetchone()
        final_qty = 0.0 if row is None else float(row[0] or 0.0)
    if buy_decision_id is None or not buy_plan_ok or buy_order_id is None or not buy_event_ok or buy_fill_id is None:
        status = "INCOMPLETE_BUY" if buy_plan_ok else "BLOCKED"
    elif sell_decision_id is None or not sell_plan_ok or sell_order_id is None or not sell_event_ok or sell_fill_id is None:
        status = "INCOMPLETE_SELL" if sell_plan_ok else "BLOCKED"
    elif lifecycle_id is None:
        status = "FAILED_LIFECYCLE"
    elif abs(final_qty) > float(min_executable_qty):
        status = "FINAL_POSITION_NOT_FLAT"
    else:
        status = "PASS"
    return {
        "artifact_type": "h74_execution_path_probe_report",
        "execution_path_probe_status": status,
        "buy_leg": {
            "decision_id": buy_decision_id,
            "execution_plan_submit_expected": buy_plan_ok,
            "order_id": buy_order_id,
            "order_event_submit": buy_event_ok,
            "fill_id": buy_fill_id,
            "open_lot_created": open_lot_created,
        },
        "sell_leg": {
            "decision_id": sell_decision_id,
            "execution_plan_submit_expected": sell_plan_ok,
            "order_id": sell_order_id,
            "order_event_submit": sell_event_ok,
            "fill_id": sell_fill_id,
            "lifecycle_id": lifecycle_id,
        },
        "final_asset_qty": final_qty,
        "research_equivalence": False,
        "research_equivalence_status": "NOT_APPLICABLE",
        "production_approval": False,
    }
