from __future__ import annotations

import sqlite3

import pytest

from bithumb_bot.strategy.daily_participation_policy import (
    DailyParticipationPolicyConfig,
    DailyParticipationStateSnapshot,
    build_research_daily_count_snapshot,
    build_runtime_daily_count_snapshot_from_sqlite,
    evaluate_daily_participation_policy,
    require_runtime_comparable_daily_count_snapshot,
)


def _config(**overrides):
    values = {
        "enabled": True,
        "timezone": "Asia/Seoul",
        "count_basis": "filled",
        "window_start_hour": 0,
        "window_end_hour": 24,
        "buy_fraction": 0.05,
        "max_order_krw": 10000.0,
    }
    values.update(overrides)
    return DailyParticipationPolicyConfig(**values)


def _state(**overrides):
    values = {
        "decision_ts": 1_704_046_800_000,
        "count_for_kst_day": 0,
        "position_open": False,
        "daily_count_snapshot_hash": "sha256:" + "4" * 64,
    }
    values.update(overrides)
    return DailyParticipationStateSnapshot(**values)


def test_research_and_runtime_daily_participation_policy_hash_match() -> None:
    research = evaluate_daily_participation_policy(config=_config(), state=_state())
    runtime = evaluate_daily_participation_policy(config=_config(), state=_state())

    assert research.participation_input_hash == runtime.participation_input_hash
    assert research.participation_policy_hash == runtime.participation_policy_hash


def test_research_and_runtime_daily_participation_policy_hash_match_with_real_adapters() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE fills (fill_ts INTEGER NOT NULL, qty REAL NOT NULL)")
    conn.execute("INSERT INTO fills(fill_ts, qty) VALUES (?, ?)", (1_704_031_200_000, 1.0))
    config = _config(count_basis="filled")
    decision_ts = 1_704_046_800_000
    research_snapshot = build_research_daily_count_snapshot(
        config=config,
        decision_ts=decision_ts,
        trade_records=(
            {
                "side": "BUY",
                "fill_ts": 1_704_031_200_000,
                "is_execution_filled": True,
            },
        ),
    )
    runtime_snapshot = build_runtime_daily_count_snapshot_from_sqlite(
        conn=conn,
        config=config,
        decision_ts=decision_ts,
        pair="KRW-BTC",
    )
    research = evaluate_daily_participation_policy(
        config=config,
        state=research_snapshot.state_snapshot(decision_ts=decision_ts, position_open=False, entry_allowed=True),
    )
    runtime = evaluate_daily_participation_policy(
        config=config,
        state=runtime_snapshot.state_snapshot(decision_ts=decision_ts, position_open=False, entry_allowed=True),
    )

    assert research.count_basis == runtime.count_basis
    assert research.kst_day == runtime.kst_day
    assert research.daily_count_snapshot_hash != "sha256:missing"
    assert runtime.daily_count_snapshot_hash != "sha256:missing"
    assert research.participation_policy_hash == runtime.participation_policy_hash
    assert research.participation_input_hash == runtime.participation_input_hash


def test_daily_count_snapshot_hash_missing_fails_runtime_comparable_mode() -> None:
    with pytest.raises(ValueError, match="daily_count_snapshot_hash_missing"):
        require_runtime_comparable_daily_count_snapshot(_state(daily_count_snapshot_hash="sha256:missing"))


def test_count_basis_mismatch_changes_policy_input_hash() -> None:
    filled = evaluate_daily_participation_policy(config=_config(count_basis="filled"), state=_state())
    intent = evaluate_daily_participation_policy(config=_config(count_basis="intent"), state=_state())

    assert filled.participation_input_hash != intent.participation_input_hash


def test_kst_day_boundary_mismatch_changes_policy_input_hash() -> None:
    first = evaluate_daily_participation_policy(config=_config(), state=_state(decision_ts=1_704_034_799_000))
    second = evaluate_daily_participation_policy(config=_config(), state=_state(decision_ts=1_704_034_800_000))

    assert first.kst_day != second.kst_day
    assert first.participation_input_hash != second.participation_input_hash
