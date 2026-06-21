from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from bithumb_bot.db_core import ensure_db
from bithumb_bot.runtime.cycle_artifact_assembler import RuntimeCycleArtifactAssembler
from bithumb_bot.runtime.decision_coordinator import DecisionCycleResult
from bithumb_bot.runtime.decision_persistence import DecisionPersistenceError, DecisionPersistenceUnitOfWork


def test_locked_db_failure_artifact_contains_subphase_sql_group_and_retry_count(tmp_path, caplog) -> None:
    db_path = tmp_path / "observability.sqlite"
    conn = ensure_db(str(db_path))
    holder = sqlite3.connect(str(db_path), timeout=0.01)
    holder.execute("BEGIN IMMEDIATE")
    uow = DecisionPersistenceUnitOfWork(retry_count=0, retry_backoff_ms=0)

    with pytest.raises(DecisionPersistenceError) as excinfo:
        uow.persist(
            conn,
            typed_bundle=SimpleNamespace(
                strategy_set=SimpleNamespace(market_scope=SimpleNamespace(pair="KRW-BTC", interval="1m"))
            ),
            planning_bundle=SimpleNamespace(execution_plan_batch=object(), planning_error=None),
            context={"portfolio_allocation_decision": {}, "execution_decision": {}, "ts": 1},
            strategy_name="s",
            signal="HOLD",
            reason="ok",
            updated_ts=1,
            settings_obj=SimpleNamespace(PAIR="KRW-BTC"),
        )

    metadata = dict(excinfo.value.metadata)
    assert metadata["db_subphase"] == "begin_immediate"
    assert metadata["sql_group"] == "decision_persistence_transaction"
    assert metadata["retry_count"] == 0
    assert metadata["transaction_elapsed_ms"] >= 0
    assert "INSERT INTO" not in caplog.text

    result = DecisionCycleResult(
        candle_ts=1,
        strategy_name="s",
        signal="HOLD",
        reason="decision_persistence_sqlite_lock",
        decision_id=None,
        decision_context=None,
        execution_decision_summary=None,
        execution_plan_bundle=None,
        strategy_decision_hash=None,
        execution_plan_bundle_hash=None,
        persistence_status="failed",
        mark_processed_candidate=False,
        failure_phase="decision persistence",
        failure_subphase="begin_immediate",
        failure_reason_code="decision_persistence_sqlite_lock",
        failure_detail=str(excinfo.value),
        persistence_failure_metadata=metadata,
        persistence_retry_count=0,
        persistence_max_retry_count=0,
        db_subphase=str(metadata["db_subphase"]),
        sql_group=str(metadata["sql_group"]),
        transaction_elapsed_ms=float(metadata["transaction_elapsed_ms"]),
        lock_wait_elapsed_ms=float(metadata["lock_wait_elapsed_ms"]),
    )
    artifact = RuntimeCycleArtifactAssembler().from_cycle_results(
        cycle_id="skip:decision_persistence_failed_retryable",
        startup_state="READY",
        decision_result=result,
    ).as_dict()

    assert artifact["db_subphase"] == "begin_immediate"
    assert artifact["sql_group"] == "decision_persistence_transaction"
    assert artifact["retry_count"] == 0
    assert artifact["transaction_elapsed_ms"] >= 0
    holder.rollback()
    holder.close()
    conn.close()
