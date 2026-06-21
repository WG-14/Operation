from __future__ import annotations

from types import SimpleNamespace

import pytest

from bithumb_bot.db_core import ensure_db
from bithumb_bot.runtime.decision_persistence import DecisionPersistenceUnitOfWork


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


def _smoke_uow(**overrides) -> DecisionPersistenceUnitOfWork:
    def bundle_fn(conn, **_kwargs):
        conn.execute(
            """
            INSERT INTO runtime_strategy_decision_bundle(
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
            INSERT INTO portfolio_allocation_decision(
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
        conn.execute(
            """
            INSERT INTO execution_plan_batch(
                batch_hash, batch_id, runtime_strategy_set_manifest_hash,
                allocation_decision_hash, budget_lock_hash, status, batch_json, created_ts
            )
            VALUES ('batch-hash', 'batch-id', 'manifest', 'alloc-hash', 'lock', 'ALLOW', '{}', 1)
            """
        )
        return {"execution_plan_batch_hash": "batch-hash", "execution_plan_batch_id": "batch-id"}

    def execution_fn(conn, **_kwargs):
        conn.execute(
            """
            INSERT INTO execution_plan(
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

    kwargs = {
        "record_runtime_strategy_decision_bundle_fn": bundle_fn,
        "record_portfolio_allocation_decision_fn": allocation_fn,
        "record_execution_plan_batch_fn": batch_fn,
        "record_execution_plan_fn": execution_fn,
        "record_strategy_decision_fn": strategy_fn,
    }
    kwargs.update(overrides)
    return DecisionPersistenceUnitOfWork(**kwargs)


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


def test_live_like_cycle_persists_decision_bundle_allocation_execution_plan_and_hands_off_to_execution(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "live-like-success.sqlite"))
    execution = SimpleNamespace(called=False)

    result = _persist(_smoke_uow(), conn)
    execution.called = result.decision_id is not None

    assert _count(conn, "runtime_strategy_decision_bundle") == 1
    assert _count(conn, "portfolio_allocation_decision") == 1
    assert _count(conn, "execution_plan") == 1
    assert _count(conn, "strategy_decisions") == 1
    assert execution.called is True
    conn.close()


def test_live_like_cycle_persistence_failure_does_not_call_execution_and_leaves_no_partial_rows(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "live-like-failure.sqlite"))
    execution = SimpleNamespace(called=False)
    uow = _smoke_uow(record_execution_plan_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        _persist(uow, conn)

    assert execution.called is False
    assert _count(conn, "runtime_strategy_decision_bundle") == 0
    assert _count(conn, "portfolio_allocation_decision") == 0
    assert _count(conn, "execution_plan") == 0
    assert _count(conn, "strategy_decisions") == 0
    conn.close()
