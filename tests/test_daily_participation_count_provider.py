from __future__ import annotations

import sqlite3

from bithumb_bot.runtime.daily_participation_count_provider import build_runtime_daily_count_snapshot_from_sqlite
from bithumb_bot.strategy.daily_participation_policy import (
    DailyParticipationPolicyConfig,
    build_research_daily_count_snapshot,
    evaluate_daily_participation_policy,
)


DECISION_TS = 1_704_046_800_000
FILL_TS = 1_704_043_200_000


def _config() -> DailyParticipationPolicyConfig:
    return DailyParticipationPolicyConfig(
        enabled=True,
        timezone="Asia/Seoul",
        count_basis="filled",
        window_start_hour=0,
        window_end_hour=24,
        buy_fraction=0.05,
        max_order_krw=10000.0,
    )


def _conn_with_scope() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE orders (
            client_order_id TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'FILLED',
            pair TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            strategy_instance_id TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fills (
            client_order_id TEXT NOT NULL,
            fill_id TEXT,
            fill_ts INTEGER NOT NULL,
            qty REAL NOT NULL
        )
        """
    )
    return conn


def _insert_fill(
    conn: sqlite3.Connection,
    *,
    client_order_id: str = "order-1",
    side: str = "BUY",
    pair: str = "KRW-BTC",
    strategy_instance_id: str = "daily:unit",
) -> None:
    conn.execute(
        """
        INSERT INTO orders(client_order_id, side, status, pair, strategy_name, strategy_instance_id)
        VALUES (?, ?, 'FILLED', ?, 'daily_participation_sma', ?)
        """,
        (client_order_id, side, pair, strategy_instance_id),
    )
    conn.execute(
        "INSERT INTO fills(client_order_id, fill_id, fill_ts, qty) VALUES (?, 'fill-1', ?, 1.0)",
        (client_order_id, FILL_TS),
    )


def _snapshot(conn: sqlite3.Connection, *, pair: str = "KRW-BTC", strategy_instance_id: str = "daily:unit"):
    return build_runtime_daily_count_snapshot_from_sqlite(
        conn=conn,
        config=_config(),
        decision_ts=DECISION_TS,
        pair=pair,
        strategy_instance_id=strategy_instance_id,
        strategy_name="daily_participation_sma",
    )


def test_runtime_filled_count_requires_buy_order_join() -> None:
    conn = _conn_with_scope()
    _insert_fill(conn)

    snapshot = _snapshot(conn)

    assert snapshot.count_for_kst_day == 1
    assert snapshot.snapshot_hash != "sha256:missing"
    assert snapshot.rows[0]["side"] == "BUY"


def test_runtime_filled_count_excludes_sell_fill() -> None:
    conn = _conn_with_scope()
    _insert_fill(conn, side="SELL")

    snapshot = _snapshot(conn)

    assert snapshot.count_for_kst_day == 0
    assert snapshot.snapshot_hash != "sha256:missing"


def test_runtime_filled_count_excludes_other_strategy_instance() -> None:
    conn = _conn_with_scope()
    _insert_fill(conn, strategy_instance_id="other:instance")

    snapshot = _snapshot(conn)

    assert snapshot.count_for_kst_day == 0


def test_runtime_filled_count_excludes_other_pair() -> None:
    conn = _conn_with_scope()
    _insert_fill(conn, pair="KRW-ETH")

    snapshot = _snapshot(conn)

    assert snapshot.count_for_kst_day == 0


def test_runtime_count_provider_fails_closed_when_scope_columns_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE fills (fill_ts INTEGER NOT NULL, qty REAL NOT NULL)")

    snapshot = build_runtime_daily_count_snapshot_from_sqlite(
        conn=conn,
        config=_config(),
        decision_ts=DECISION_TS,
        pair="KRW-BTC",
        strategy_instance_id="daily:unit",
    )

    assert snapshot.snapshot_hash == "sha256:missing"
    assert snapshot.fail_closed_reason.startswith("daily_participation_runtime_count_source_unavailable")


def test_research_and_runtime_events_reduce_to_same_policy_decision() -> None:
    config = _config()
    research = build_research_daily_count_snapshot(
        config=config,
        decision_ts=DECISION_TS,
        pair="KRW-BTC",
        strategy_instance_id="daily:unit",
        trade_records=(
            {
                "side": "BUY",
                "client_order_id": "order-1",
                "fill_id": "fill-1",
                "fill_ts": FILL_TS,
                "is_execution_filled": True,
            },
        ),
    )
    conn = _conn_with_scope()
    _insert_fill(conn)
    runtime = _snapshot(conn)

    research_decision = evaluate_daily_participation_policy(
        config=config,
        state=research.state_snapshot(
            decision_ts=DECISION_TS,
            position_open=False,
            entry_allowed=True,
        ),
    )
    runtime_decision = evaluate_daily_participation_policy(
        config=config,
        state=runtime.state_snapshot(
            decision_ts=DECISION_TS,
            position_open=False,
            entry_allowed=True,
        ),
    )

    assert research.count_for_kst_day == runtime.count_for_kst_day == 1
    assert research_decision.allowed == runtime_decision.allowed
    assert research_decision.reason_code == runtime_decision.reason_code


def test_pending_claim_changes_participation_input_hash() -> None:
    config = _config()
    snapshot = build_research_daily_count_snapshot(
        config=config,
        decision_ts=DECISION_TS,
        pair="KRW-BTC",
        strategy_instance_id="daily:unit",
    )
    no_claim = evaluate_daily_participation_policy(
        config=config,
        state=snapshot.state_snapshot(decision_ts=DECISION_TS, position_open=False, entry_allowed=True),
    )
    with_claim = evaluate_daily_participation_policy(
        config=config,
        state=snapshot.state_snapshot(
            decision_ts=DECISION_TS,
            position_open=False,
            entry_allowed=True,
        ).__class__(
            **{
                **snapshot.state_snapshot(
                    decision_ts=DECISION_TS,
                    position_open=False,
                    entry_allowed=True,
                ).__dict__,
                "pending_claim_count": 1,
            }
        ),
    )

    assert no_claim.participation_input_hash != with_claim.participation_input_hash
    assert with_claim.reason_code == "daily_participation_pending_claim_exists"


def test_missing_strategy_instance_scope_fails_closed() -> None:
    conn = _conn_with_scope()

    snapshot = build_runtime_daily_count_snapshot_from_sqlite(
        conn=conn,
        config=_config(),
        decision_ts=DECISION_TS,
        pair="KRW-BTC",
        strategy_instance_id="",
    )

    assert snapshot.snapshot_hash == "sha256:missing"
    assert "daily_participation_strategy_instance_scope_missing" in snapshot.fail_closed_reason


def test_partial_fill_policy_is_hash_bound() -> None:
    base = _config()
    changed = DailyParticipationPolicyConfig(
        enabled=base.enabled,
        timezone=base.timezone,
        count_basis=base.count_basis,
        window_start_hour=base.window_start_hour,
        window_end_hour=base.window_end_hour,
        buy_fraction=base.buy_fraction,
        max_order_krw=base.max_order_krw,
        partial_fill_counts_as_fulfilled=True,
    )

    assert base.policy_hash() != changed.policy_hash()


def test_terminal_failed_retry_policy_is_hash_bound() -> None:
    base = _config()
    changed = DailyParticipationPolicyConfig(
        enabled=base.enabled,
        timezone=base.timezone,
        count_basis=base.count_basis,
        window_start_hour=base.window_start_hour,
        window_end_hour=base.window_end_hour,
        buy_fraction=base.buy_fraction,
        max_order_krw=base.max_order_krw,
        retry_terminal_failed_claims=True,
    )

    assert base.policy_hash() != changed.policy_hash()
