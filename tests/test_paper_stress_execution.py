from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_bot.broker import paper
from bithumb_bot.config import settings
from bithumb_bot.db_core import ensure_db, set_portfolio
from bithumb_bot.public_api_orderbook import BestQuote


def _set(attr: str, value):
    old = getattr(settings, attr)
    object.__setattr__(settings, attr, value)
    return old


def _configure_stress(
    tmp_path: Path,
    *,
    db_name: str,
    failure_rate: float = 0.0,
    partial_rate: float = 0.0,
    partial_fraction: float = 0.5,
    seed: str = "123",
):
    return {
        "DB_PATH": _set("DB_PATH", str(tmp_path / db_name)),
        "PAPER_EXECUTION_MODEL": _set("PAPER_EXECUTION_MODEL", "stress"),
        "PAPER_EXECUTION_STRESS_SEED": _set("PAPER_EXECUTION_STRESS_SEED", seed),
        "PAPER_EXECUTION_LATENCY_MS": _set("PAPER_EXECUTION_LATENCY_MS", 250),
        "PAPER_EXECUTION_PARTIAL_FILL_RATE": _set("PAPER_EXECUTION_PARTIAL_FILL_RATE", partial_rate),
        "PAPER_EXECUTION_PARTIAL_FILL_FRACTION": _set("PAPER_EXECUTION_PARTIAL_FILL_FRACTION", partial_fraction),
        "PAPER_EXECUTION_ORDER_FAILURE_RATE": _set("PAPER_EXECUTION_ORDER_FAILURE_RATE", failure_rate),
        "SLIPPAGE_BPS": _set("SLIPPAGE_BPS", 0.0),
        "MAX_ORDER_KRW": _set("MAX_ORDER_KRW", 0.0),
        "PAPER_FEE_RATE": _set("PAPER_FEE_RATE", 0.0),
        "BUY_FRACTION": _set("BUY_FRACTION", 1.0),
        "MAX_ORDERBOOK_SPREAD_BPS": _set("MAX_ORDERBOOK_SPREAD_BPS", 100.0),
    }


def _restore(values: dict[str, object]) -> None:
    for key, value in values.items():
        _set(key, value)


def _prepare_buy(monkeypatch) -> None:
    monkeypatch.setattr(
        paper,
        "fetch_orderbook_top",
        lambda _pair: BestQuote(market="KRW-BTC", bid_price=100.0, ask_price=100.0),
    )
    conn = ensure_db()
    set_portfolio(conn, cash_krw=1_000_000, asset_qty=0.0)
    conn.close()


def _latest_stress_evidence(conn):
    row = conn.execute(
        """
        SELECT submit_evidence
        FROM order_events
        WHERE submit_phase='paper_execution'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    return json.loads(str(row["submit_evidence"]))


def test_stress_failure_records_failed_order_without_fill_or_trade(tmp_path: Path, monkeypatch):
    old = _configure_stress(tmp_path, db_name="stress_failure.sqlite", failure_rate=1.0)
    try:
        _prepare_buy(monkeypatch)

        trade = paper.paper_execute("BUY", ts=1_700_000_000_000, price=100.0)

        assert trade is None
        conn = ensure_db()
        order = conn.execute(
            "SELECT status, qty_req, qty_filled FROM orders ORDER BY id DESC LIMIT 1"
        ).fetchone()
        fill_count = conn.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]
        trade_count = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        dedup_count = conn.execute("SELECT COUNT(*) AS n FROM order_intent_dedup").fetchone()["n"]
        evidence = _latest_stress_evidence(conn)
        conn.close()

        assert order is not None
        assert order["status"] == "FAILED"
        assert float(order["qty_req"]) > 0.0
        assert float(order["qty_filled"]) == pytest.approx(0.0)
        assert fill_count == 0
        assert trade_count == 0
        assert dedup_count == 0
        assert evidence["fill_status"] == "failed"
        assert evidence["filled_qty"] == pytest.approx(0.0)
    finally:
        _restore(old)


def test_stress_partial_fill_keeps_order_open_and_dedup_claimed(tmp_path: Path, monkeypatch):
    old = _configure_stress(
        tmp_path,
        db_name="stress_partial.sqlite",
        partial_rate=1.0,
        partial_fraction=0.5,
    )
    try:
        _prepare_buy(monkeypatch)

        trade = paper.paper_execute("BUY", ts=1_700_000_000_000, price=100.0)

        assert trade is not None
        conn = ensure_db()
        order = conn.execute(
            "SELECT client_order_id, status, qty_req, qty_filled FROM orders ORDER BY id DESC LIMIT 1"
        ).fetchone()
        fill = conn.execute("SELECT qty FROM fills ORDER BY id DESC LIMIT 1").fetchone()
        trade_row = conn.execute("SELECT qty FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        dedup = conn.execute("SELECT order_status FROM order_intent_dedup").fetchone()
        evidence = _latest_stress_evidence(conn)
        conn.close()

        assert order is not None
        assert order["status"] == "PARTIAL"
        assert float(order["qty_filled"]) == pytest.approx(float(order["qty_req"]) * 0.5)
        assert float(order["qty_filled"]) < float(order["qty_req"])
        assert fill is not None
        assert float(fill["qty"]) == pytest.approx(float(order["qty_filled"]))
        assert trade_row is not None
        assert float(trade_row["qty"]) == pytest.approx(float(order["qty_filled"]))
        assert dedup is not None
        assert dedup["order_status"] == "PARTIAL"
        assert evidence["fill_status"] == "partial"
        assert evidence["remaining_qty"] > 0.0
    finally:
        _restore(old)


def test_stress_execution_is_deterministic_across_isolated_dbs(tmp_path: Path, monkeypatch):
    observed: list[dict[str, object]] = []
    old = _configure_stress(
        tmp_path,
        db_name="stress_replay_1.sqlite",
        partial_rate=1.0,
        partial_fraction=0.25,
        seed="777",
    )
    try:
        for index in range(2):
            _set("DB_PATH", str(tmp_path / f"stress_replay_{index}.sqlite"))
            _prepare_buy(monkeypatch)
            trade = paper.paper_execute("BUY", ts=1_700_000_000_000, price=100.0)
            assert trade is not None
            conn = ensure_db()
            evidence = _latest_stress_evidence(conn)
            conn.close()
            observed.append(
                {
                    "fill_status": evidence["fill_status"],
                    "filled_qty": evidence["filled_qty"],
                    "remaining_qty": evidence["remaining_qty"],
                    "execution_model_params_hash": evidence["execution_model_params_hash"],
                    "derived_seed_hash": evidence["derived_seed_hash"],
                }
            )
    finally:
        _restore(old)

    assert observed[0] == observed[1]


def test_partial_stress_order_blocks_duplicate_intent(tmp_path: Path, monkeypatch):
    old = _configure_stress(
        tmp_path,
        db_name="stress_duplicate.sqlite",
        partial_rate=1.0,
        partial_fraction=0.5,
    )
    try:
        _prepare_buy(monkeypatch)

        first = paper.paper_execute("BUY", ts=1_700_000_000_000, price=100.0)
        second = paper.paper_execute("BUY", ts=1_700_000_000_000, price=100.0)

        assert first is not None
        assert second is None
        conn = ensure_db()
        order_count = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        dedup = conn.execute("SELECT order_status FROM order_intent_dedup").fetchone()
        conn.close()

        assert order_count == 1
        assert dedup is not None
        assert dedup["order_status"] == "PARTIAL"
    finally:
        _restore(old)
