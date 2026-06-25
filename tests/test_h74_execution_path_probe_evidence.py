from __future__ import annotations

import sqlite3

from bithumb_bot.h74_execution_path_probe import generate_h74_execution_path_probe_report


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE strategy_decisions(id INTEGER PRIMARY KEY, signal TEXT);
        CREATE TABLE execution_plan(id INTEGER PRIMARY KEY, side TEXT, submit_expected INTEGER);
        CREATE TABLE orders(id INTEGER PRIMARY KEY, client_order_id TEXT, side TEXT);
        CREATE TABLE order_events(id INTEGER PRIMARY KEY, side TEXT, event_type TEXT, exception_class TEXT);
        CREATE TABLE fills(id INTEGER PRIMARY KEY, client_order_id TEXT);
        CREATE TABLE open_position_lots(id INTEGER PRIMARY KEY, pair TEXT);
        CREATE TABLE trade_lifecycles(id INTEGER PRIMARY KEY, pair TEXT);
        CREATE TABLE portfolio(id INTEGER PRIMARY KEY, asset_qty REAL);
        INSERT INTO portfolio(id, asset_qty) VALUES(1, 0);
        """
    )
    return conn


def _seed_buy(conn: sqlite3.Connection, *, blocked: bool = False) -> None:
    conn.execute("INSERT INTO strategy_decisions(id, signal) VALUES(1, 'BUY')")
    conn.execute("INSERT INTO execution_plan(id, side, submit_expected) VALUES(1, 'BUY', ?)", (0 if blocked else 1,))
    conn.execute("INSERT INTO orders(id, client_order_id, side) VALUES(1, 'buy-1', 'BUY')")
    conn.execute("INSERT INTO order_events(id, side, event_type, exception_class) VALUES(1, 'BUY', 'submit', '')")
    conn.execute("INSERT INTO fills(id, client_order_id) VALUES(1, 'buy-1')")
    conn.execute("INSERT INTO open_position_lots(id, pair) VALUES(1, 'KRW-BTC')")


def _seed_sell(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO strategy_decisions(id, signal) VALUES(2, 'SELL')")
    conn.execute("INSERT INTO execution_plan(id, side, submit_expected) VALUES(2, 'SELL', 1)")
    conn.execute("INSERT INTO orders(id, client_order_id, side) VALUES(2, 'sell-1', 'SELL')")
    conn.execute("INSERT INTO order_events(id, side, event_type, exception_class) VALUES(2, 'SELL', 'submit', '')")
    conn.execute("INSERT INTO fills(id, client_order_id) VALUES(2, 'sell-1')")


def test_probe_report_requires_buy_decision_plan_order_fill() -> None:
    report = generate_h74_execution_path_probe_report(_conn())
    assert report["execution_path_probe_status"] in {"BLOCKED", "INCOMPLETE_BUY"}


def test_probe_report_requires_sell_decision_plan_order_fill() -> None:
    conn = _conn()
    _seed_buy(conn)
    report = generate_h74_execution_path_probe_report(conn)
    assert report["execution_path_probe_status"] == "BLOCKED"


def test_probe_report_requires_closed_lifecycle() -> None:
    conn = _conn()
    _seed_buy(conn)
    _seed_sell(conn)
    report = generate_h74_execution_path_probe_report(conn)
    assert report["execution_path_probe_status"] == "FAILED_LIFECYCLE"


def test_probe_report_final_position_must_be_flat_or_dust() -> None:
    conn = _conn()
    _seed_buy(conn)
    _seed_sell(conn)
    conn.execute("INSERT INTO trade_lifecycles(id, pair) VALUES(1, 'KRW-BTC')")
    conn.execute("UPDATE portfolio SET asset_qty=0.5 WHERE id=1")
    report = generate_h74_execution_path_probe_report(conn, min_executable_qty=0.0001)
    assert report["execution_path_probe_status"] == "FINAL_POSITION_NOT_FLAT"


def test_probe_report_classifies_blocked_plan() -> None:
    conn = _conn()
    _seed_buy(conn, blocked=True)
    report = generate_h74_execution_path_probe_report(conn)
    assert report["execution_path_probe_status"] == "BLOCKED"


def test_probe_report_pass_includes_buy_sell_identifiers() -> None:
    conn = _conn()
    _seed_buy(conn)
    _seed_sell(conn)
    conn.execute("INSERT INTO trade_lifecycles(id, pair) VALUES(1, 'KRW-BTC')")
    report = generate_h74_execution_path_probe_report(conn)
    assert report["execution_path_probe_status"] == "PASS"
    assert report["buy_leg"]["decision_id"] == 1
    assert report["sell_leg"]["lifecycle_id"] == 1
    assert report["research_equivalence"] is False
    assert report["production_approval"] is False
