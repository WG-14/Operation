from __future__ import annotations

from types import SimpleNamespace

import pytest

from operation.db_core import ensure_db
from operation.runtime.decision_persistence import DecisionPersistenceUnitOfWork


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_set=SimpleNamespace(
            market_scope=SimpleNamespace(pair="KRW-BTC", interval="1m"),
        )
    )


def _planning_bundle() -> SimpleNamespace:
    return SimpleNamespace(execution_plan_batch=object(), planning_error=None)


def _context() -> dict[str, object]:
    return {
        "ts": 1,
        "last_close": 100.0,
        "execution_decision": {},
        "portfolio_allocation_decision": {"allocation_decision_hash": "alloc-hash"},
    }


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _uow(**overrides):
    calls = {"bundle": 0, "allocation": 0, "batch": 0, "execution": 0}

    def bundle_fn(conn, **_kwargs):
        calls["bundle"] += 1
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
            "runtime_strategy_set_manifest_id": None,
        }

    def allocation_fn(conn, **_kwargs):
        calls["allocation"] += 1
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
        calls["batch"] += 1
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
        calls["execution"] += 1
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
            """
            INSERT INTO strategy_decisions(decision_ts, strategy_name, signal, reason, context_json)
            VALUES (1, 's', 'HOLD', 'ok', '{}')
            """
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
    return DecisionPersistenceUnitOfWork(**kwargs), calls


def test_decision_persistence_commits_all_rows_atomically(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "uow-success.sqlite"))
    uow, _calls = _uow()

    result = uow.persist(
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

    assert result.decision_id == 1
    assert _count(conn, "runtime_strategy_decision_bundle") == 1
    assert _count(conn, "portfolio_allocation_decision") == 1
    assert _count(conn, "execution_plan") == 1
    assert _count(conn, "strategy_decisions") == 1
    conn.close()


def test_decision_persistence_rolls_back_runtime_bundle_on_allocation_failure(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "uow-allocation-fail.sqlite"))
    base, _calls = _uow(
        record_portfolio_allocation_decision_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("allocation"))
    )

    with pytest.raises(RuntimeError, match="allocation"):
        base.persist(
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

    assert _count(conn, "runtime_strategy_decision_bundle") == 0
    assert _count(conn, "portfolio_allocation_decision") == 0
    conn.close()


def test_decision_persistence_rolls_back_all_rows_on_execution_plan_failure(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "uow-plan-fail.sqlite"))
    uow, _calls = _uow(
        record_execution_plan_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("execution"))
    )

    with pytest.raises(RuntimeError, match="execution"):
        uow.persist(
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

    assert _count(conn, "runtime_strategy_decision_bundle") == 0
    assert _count(conn, "portfolio_allocation_decision") == 0
    assert _count(conn, "execution_plan") == 0
    conn.close()


def test_decision_persistence_rolls_back_target_state_and_locks_on_late_failure(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "uow-late-fail.sqlite"))

    def failing_order_lock(conn, **kwargs):
        from operation.db_core import create_or_get_order_lock

        create_or_get_order_lock(conn, **kwargs)
        raise RuntimeError("late")

    uow, _calls = _uow(order_lock_persister=failing_order_lock)
    context = {
        **_context(),
        "target_state_update_intent": {
            "pair": "KRW-BTC",
            "target_exposure_krw": 0.0,
            "target_qty": 0.0,
            "last_signal": "HOLD",
            "last_reference_price": 100.0,
            "updated_ts": 1,
        },
        "lock_intents": [
            {
                "lock_kind": "order",
                "pair": "KRW-BTC",
                "currency": "BTC",
                "amount": 1.0,
                "reason": "test",
                "created_ts": 1,
                "idempotency_key": "key",
                "evidence": {},
            }
        ],
    }

    with pytest.raises(RuntimeError, match="late"):
        uow.persist(
            conn,
            typed_bundle=_bundle(),
            planning_bundle=_planning_bundle(),
            context=context,
            strategy_name="s",
            signal="HOLD",
            reason="ok",
            updated_ts=1,
            settings_obj=SimpleNamespace(PAIR="KRW-BTC"),
        )

    assert _count(conn, "target_position_state") == 0
    assert _count(conn, "order_locks") == 0
    assert _count(conn, "strategy_decisions") == 0
    conn.close()
