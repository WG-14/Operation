from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bithumb_bot.h74_state_cleanup import H74StateCleanupError, clear_h74_non_authoritative_state


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE portfolio(id INTEGER PRIMARY KEY, asset_qty REAL);
        INSERT INTO portfolio(id, asset_qty) VALUES(1, 0);
        CREATE TABLE open_position_lots(id INTEGER PRIMARY KEY, pair TEXT, qty_open REAL);
        CREATE TABLE orders(id INTEGER PRIMARY KEY, pair TEXT, status TEXT);
        CREATE TABLE fills(id INTEGER PRIMARY KEY);
        CREATE TABLE trades(id INTEGER PRIMARY KEY);
        CREATE TABLE trade_lifecycles(id INTEGER PRIMARY KEY);
        CREATE TABLE target_position_state(pair TEXT PRIMARY KEY);
        CREATE TABLE strategy_virtual_target_state(strategy_instance_id TEXT, strategy_name TEXT, pair TEXT);
        """
    )
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO target_position_state(pair) VALUES('KRW-BTC')")
    conn.execute(
        "INSERT INTO strategy_virtual_target_state(strategy_instance_id,strategy_name,pair) VALUES('h74','daily_participation_sma','KRW-BTC')"
    )
    conn.execute("INSERT INTO target_position_state(pair) VALUES('KRW-ETH')")
    conn.execute("INSERT INTO fills(id) VALUES(1)")
    conn.execute("INSERT INTO trades(id) VALUES(1)")
    conn.execute("INSERT INTO trade_lifecycles(id) VALUES(1)")


def test_cleanup_deletes_only_target_and_h74_virtual_when_flat(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    summary = clear_h74_non_authoritative_state(
        conn,
        pair="KRW-BTC",
        backup_path=tmp_path / "backup.json",
        broker_convergence_ok=True,
    )
    assert summary["after"]["target_position_state_count"] == 0
    assert summary["after"]["h74_virtual_target_state_count"] == 0
    assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-ETH'").fetchone()[0] == 1


def test_cleanup_refuses_when_portfolio_asset_qty_nonzero(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    conn.execute("UPDATE portfolio SET asset_qty=0.1 WHERE id=1")
    with pytest.raises(H74StateCleanupError, match="asset_qty_nonzero"):
        clear_h74_non_authoritative_state(conn, pair="KRW-BTC", backup_path=tmp_path / "b.json", broker_convergence_ok=True)


def test_cleanup_refuses_when_open_lot_exists(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    conn.execute("INSERT INTO open_position_lots(pair, qty_open) VALUES('KRW-BTC', 0.1)")
    with pytest.raises(H74StateCleanupError, match="open_lot_exists"):
        clear_h74_non_authoritative_state(conn, pair="KRW-BTC", backup_path=tmp_path / "b.json", broker_convergence_ok=True)


def test_cleanup_refuses_when_risky_order_exists(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    conn.execute("INSERT INTO orders(pair, status) VALUES('KRW-BTC', 'open')")
    with pytest.raises(H74StateCleanupError, match="risky_order_exists"):
        clear_h74_non_authoritative_state(conn, pair="KRW-BTC", backup_path=tmp_path / "b.json", broker_convergence_ok=True)


def test_cleanup_never_deletes_orders_fills_trades_lifecycles(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    conn.execute("INSERT INTO orders(pair, status) VALUES('KRW-ETH', 'closed')")
    before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("orders", "fills", "trades", "trade_lifecycles")}
    clear_h74_non_authoritative_state(conn, pair="KRW-BTC", backup_path=tmp_path / "b.json", broker_convergence_ok=True)
    after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("orders", "fills", "trades", "trade_lifecycles")}
    assert after == before


def test_cleanup_writes_backup_and_before_after_summary(tmp_path: Path) -> None:
    conn = _conn()
    _seed(conn)
    backup = tmp_path / "backup.json"
    summary = clear_h74_non_authoritative_state(conn, pair="KRW-BTC", backup_path=backup, broker_convergence_ok=True)
    assert backup.exists()
    assert summary["before"]["target_position_state_count"] == 1
    assert summary["after"]["target_position_state_count"] == 0
