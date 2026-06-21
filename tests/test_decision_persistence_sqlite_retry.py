from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from bithumb_bot.db_core import ensure_db
from bithumb_bot.runtime.decision_persistence import (
    DecisionPersistenceError,
    DecisionPersistenceUnitOfWork,
)


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(strategy_set=SimpleNamespace(market_scope=SimpleNamespace(pair="KRW-BTC", interval="1m")))


def _planning_bundle() -> SimpleNamespace:
    return SimpleNamespace(execution_plan_batch=object(), planning_error=None)


def _context() -> dict[str, object]:
    return {
        "ts": 1,
        "last_close": 100.0,
        "execution_decision": {},
        "portfolio_allocation_decision": {"allocation_decision_hash": "alloc-hash"},
    }


def _simple_uow(*, retry_count: int = 1, retry_backoff_ms: int = 1) -> DecisionPersistenceUnitOfWork:
    def bundle_fn(conn, **_kwargs):
        conn.execute(
            """
            INSERT OR IGNORE INTO runtime_strategy_decision_bundle(
                candle_ts, pair, interval, strategy_set_manifest_hash,
                bundle_hash, result_count, created_ts
            )
            VALUES (1, 'KRW-BTC', '1m', 'manifest', 'bundle-hash', 1, 1)
            """
        )
        return {
            "runtime_strategy_decision_bundle_id": 1,
            "runtime_strategy_decision_bundle_hash": "bundle-hash",
            "runtime_strategy_set_manifest_hash": "manifest",
        }

    def allocation_fn(conn, **_kwargs):
        conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_allocation_decision(
                bundle_id, allocation_decision_hash, allocation_input_hash,
                allocator_config_hash, strategy_contribution_hash, authoritative,
                primary_block_reason, reason, conflict_resolution_json,
                allocation_decision_json
            )
            VALUES (1, 'alloc-hash', 'input', 'config', 'contrib', 1, '', 'ok', '{}', '{}')
            """
        )
        return {
            "portfolio_allocation_decision_id": 1,
            "allocation_decision_hash": "alloc-hash",
            "portfolio_target_id": None,
            "portfolio_target_hash": "",
        }

    def batch_fn(conn, **_kwargs):
        return {"execution_plan_batch_hash": "batch-hash", "execution_plan_batch_id": "batch-id"}

    def execution_fn(conn, **_kwargs):
        conn.execute(
            """
            INSERT OR IGNORE INTO execution_plan(
                allocation_id, portfolio_target_hash, execution_plan_bundle_hash,
                execution_submit_plan_hash, submit_expected, final_action,
                block_reason, status, execution_plan_bundle_json
            )
            VALUES (1, '', 'plan-hash', 'submit-hash', 0, 'HOLD', '', 'NOT_REQUIRED', '{}')
            """
        )
        return {
            "execution_plan_id": 1,
            "execution_plan_bundle_hash": "plan-hash",
            "execution_submit_plan_hash": "submit-hash",
        }

    def strategy_fn(conn, **_kwargs):
        conn.execute(
            "INSERT INTO strategy_decisions(decision_ts, strategy_name, signal, reason, context_json) VALUES (1, 's', 'HOLD', 'ok', '{}')"
        )
        return 1

    return DecisionPersistenceUnitOfWork(
        record_runtime_strategy_decision_bundle_fn=bundle_fn,
        record_portfolio_allocation_decision_fn=allocation_fn,
        record_execution_plan_batch_fn=batch_fn,
        record_execution_plan_fn=execution_fn,
        record_strategy_decision_fn=strategy_fn,
        retry_count=retry_count,
        retry_backoff_ms=retry_backoff_ms,
    )


def _persist(uow: DecisionPersistenceUnitOfWork, conn):
    return uow.persist(
        conn,
        typed_bundle=_bundle(),
        planning_bundle=_planning_bundle(),
        context=_context(),
        strategy_name="s",
        signal="HOLD",
        reason="ok",
        updated_ts=1,
        settings_obj=SimpleNamespace(PAIR="KRW-BTC"),
    )


def test_decision_persistence_retries_whole_transaction_after_database_locked(tmp_path) -> None:
    db_path = tmp_path / "locked-retry.sqlite"
    conn = ensure_db(str(db_path))
    holder = sqlite3.connect(str(db_path), timeout=0.01)
    holder.execute("BEGIN IMMEDIATE")
    uow = _simple_uow(retry_count=1, retry_backoff_ms=1)

    with pytest.raises(DecisionPersistenceError) as excinfo:
        _persist(uow, conn)

    metadata = dict(excinfo.value.metadata)
    assert metadata["retry_count"] == 1
    assert metadata["max_retry_count"] == 1
    assert metadata["db_subphase"] == "begin_immediate"
    assert metadata["last_lock_error"]
    assert _count(conn, "runtime_strategy_decision_bundle") == 0
    holder.rollback()
    holder.close()
    conn.close()


def test_decision_persistence_retry_success_does_not_duplicate_rows(tmp_path) -> None:
    db_path = tmp_path / "retry-success.sqlite"
    conn = ensure_db(str(db_path))
    attempts = {"n": 0}
    uow = _simple_uow(retry_count=1, retry_backoff_ms=0)
    original = uow.record_runtime_strategy_decision_bundle_fn

    def flaky_bundle(conn, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(conn, **kwargs)

    uow.record_runtime_strategy_decision_bundle_fn = flaky_bundle

    result = _persist(uow, conn)

    assert result.retry_count == 1
    assert _count(conn, "runtime_strategy_decision_bundle") == 1
    assert _count(conn, "portfolio_allocation_decision") == 1
    assert _count(conn, "execution_plan") == 1
    conn.close()
