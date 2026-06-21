from __future__ import annotations

from types import SimpleNamespace

from bithumb_bot.db_core import ensure_db
from bithumb_bot.execution_service import ExecutionSubmitPlan
from bithumb_bot.run_loop_execution_planner import (
    _build_execution_plan_batch_for_runtime_pair,
    resolve_target_position_state_for_run_loop,
)


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _readiness_payload() -> dict[str, object]:
    return {
        "residual_inventory_qty": 0.0,
        "residual_inventory_notional_krw": 0.0,
        "residual_inventory_state": "flat",
        "residual_inventory_policy_allows_buy": True,
        "residual_inventory_policy_allows_sell": False,
        "residual_inventory_policy_allows_run": True,
        "cash_available": 1_000_000.0,
        "broker_position_evidence": {
            "broker_qty_known": True,
            "broker_qty": 0.0,
            "balance_source_stale": False,
        },
        "projection_converged": True,
        "projection_convergence": {"converged": True},
        "broker_portfolio_converged": True,
        "open_order_count": 0,
        "accounting_projection_ok": True,
        "active_fee_accounting_blocker": False,
        "residual_proof_min_qty": 0.0001,
        "residual_proof_min_notional_krw": 5000.0,
    }


def test_plan_runtime_strategy_results_does_not_insert_target_state(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "planner-target.sqlite"))

    result = resolve_target_position_state_for_run_loop(
        conn,
        readiness_payload=_readiness_payload(),
        reference_price=100_000_000.0,
        raw_signal="HOLD",
        updated_ts=1,
        settings_obj=SimpleNamespace(PAIR="KRW-BTC", EXECUTION_ENGINE="target_delta"),
        runtime_pair="KRW-BTC",
    )

    assert _count(conn, "target_position_state") == 0
    assert isinstance(result["target_policy_metadata"], dict)
    assert isinstance(result["target_policy_metadata"].get("target_state_update_intent"), dict)
    conn.close()


def test_buy_submit_plan_returns_budget_lock_intent_without_budget_lock_row(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "planner-buy-lock.sqlite"))
    context = {"runtime_pair": "KRW-BTC", "portfolio_target_hash": "target-hash"}
    submit_plan = ExecutionSubmitPlan(
        side="BUY",
        source="test",
        authority="test",
        final_action="BUY",
        qty=0.001,
        notional_krw=10000.0,
        target_exposure_krw=10000.0,
        current_effective_exposure_krw=0.0,
        delta_krw=10000.0,
        submit_expected=True,
        pre_submit_proof_status="not_required",
        block_reason="",
        idempotency_key="buy-key",
        pair="KRW-BTC",
    )

    batch = _build_execution_plan_batch_for_runtime_pair(
        conn,
        context=context,
        submit_plan=submit_plan,
        updated_ts=1,
    )

    assert _count(conn, "budget_locks") == 0
    assert context["lock_intents"][0]["lock_kind"] == "budget"
    assert batch.pair_plans[0].lock_status == "intent_pending_persistence"
    conn.close()


def test_sell_submit_plan_returns_order_lock_intent_without_order_lock_row(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "planner-sell-lock.sqlite"))
    context = {"runtime_pair": "KRW-BTC", "portfolio_target_hash": "target-hash"}
    submit_plan = ExecutionSubmitPlan(
        side="SELL",
        source="test",
        authority="test",
        final_action="SELL",
        qty=0.001,
        notional_krw=None,
        target_exposure_krw=0.0,
        current_effective_exposure_krw=10000.0,
        delta_krw=-10000.0,
        submit_expected=True,
        pre_submit_proof_status="not_required",
        block_reason="",
        idempotency_key="sell-key",
        pair="KRW-BTC",
    )

    batch = _build_execution_plan_batch_for_runtime_pair(
        conn,
        context=context,
        submit_plan=submit_plan,
        updated_ts=1,
    )

    assert _count(conn, "order_locks") == 0
    assert context["lock_intents"][0]["lock_kind"] == "order"
    assert batch.pair_plans[0].lock_status == "intent_pending_persistence"
    conn.close()


def test_planner_source_has_no_forbidden_write_calls() -> None:
    source = "src/bithumb_bot/run_loop_execution_planner.py"
    text = open(source, encoding="utf-8").read()
    forbidden = (
        "upsert_target_position_state",
        "upsert_strategy_virtual_target_state",
        "create_or_get_budget_lock",
        "create_or_get_order_lock",
        "conn.commit",
    )
    for needle in forbidden:
        assert needle not in text
