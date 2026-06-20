from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .config import runtime_code_provenance, settings
from .db_core import ensure_db, record_strategy_decision
from .execution_service import ExecutionDecisionSummary, ExecutionSubmitPlan, build_signal_execution_service
from .live_pipeline_smoke_authority import (
    LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
    LIVE_PIPELINE_SMOKE_CYCLES,
    LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    LIVE_PIPELINE_SMOKE_MAX_ORDERS,
    build_live_pipeline_smoke_plan_payload,
    load_live_pipeline_smoke_authority,
)
from .live_pipeline_smoke_preflight import (
    LivePipelineSmokePreflightError,
    LivePipelineSmokeReadiness,
    readiness_from_snapshot,
    validate_live_pipeline_smoke_start_preflight,
    validate_live_pipeline_smoke_step_readiness,
)
from .runtime_readiness import compute_runtime_readiness_snapshot
from .runtime.execution_coordinator import ExecutionCoordinator, build_signal_execution_request
from .runtime.live_pipeline_smoke_decision import (
    OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME,
    LivePipelineSmokeDecisionProvider,
)


class LivePipelineSmokeError(ValueError):
    pass


@dataclass
class LivePipelineSmokeExecutionService:
    broker: Any
    reference_price: float = 100_000_000.0
    fail_at_step: int | None = None
    submissions: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, request: Any) -> dict[str, Any] | None:
        if self.fail_at_step is not None and len(self.submissions) == int(self.fail_at_step):
            raise RuntimeError("fake_broker_submit_failure")
        summary = request.execution_decision_summary
        plan = summary.typed_target_submit_plan() if isinstance(summary, ExecutionDecisionSummary) else None
        if plan is None:
            return None
        side = str(plan.side).upper()
        qty = float(plan.qty or 0.0)
        client_order_id = f"lps_{int(request.ts)}_{side.lower()}_{len(self.submissions) + 1}"
        exchange_order_id = f"ex_lps_{len(self.submissions) + 1}"
        if hasattr(self.broker, "apply_fill"):
            self.broker.apply_fill(side=side, qty=qty)
        submission = {
            "status": "submitted",
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id,
            "side": side,
            "filled_qty": qty,
            "submit_qty": qty,
            "decision_id": request.decision_id,
        }
        self.submissions.append(submission)
        return submission


def validate_live_pipeline_smoke_request(
    *,
    apply: bool,
    yes: bool,
    cycles: int,
    max_orders: int,
    max_notional_krw: float,
    authority_path: str | None,
    confirm: str | None,
    mode: str | None = None,
) -> None:
    live = str(mode if mode is not None else settings.MODE).strip().lower() == "live"
    if live and int(cycles) != LIVE_PIPELINE_SMOKE_CYCLES:
        raise LivePipelineSmokeError("live_pipeline_smoke_live_cycles_must_be_5")
    if int(max_orders) != int(cycles) * 2:
        raise LivePipelineSmokeError("live_pipeline_smoke_max_orders_must_equal_cycles_x2")
    if live and int(max_orders) > LIVE_PIPELINE_SMOKE_MAX_ORDERS:
        raise LivePipelineSmokeError("live_pipeline_smoke_live_max_orders_above_10")
    if float(max_notional_krw) <= 0.0:
        raise LivePipelineSmokeError("live_pipeline_smoke_max_notional_must_be_positive")
    if apply:
        if not yes:
            raise LivePipelineSmokeError("live_pipeline_smoke_apply_requires_yes")
        if not str(authority_path or "").strip():
            raise LivePipelineSmokeError("live_pipeline_smoke_apply_requires_authority_path")
        if str(confirm or "") != LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN:
            raise LivePipelineSmokeError("live_pipeline_smoke_apply_requires_confirmation_token")


def build_live_pipeline_smoke_plan(
    *,
    cycles: int,
    max_orders: int,
    max_notional_krw: float,
    market: str | None = None,
) -> dict[str, Any]:
    validate_live_pipeline_smoke_request(
        apply=False,
        yes=False,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        authority_path=None,
        confirm=None,
    )
    return build_live_pipeline_smoke_plan_payload(
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        market=str(market or settings.PAIR),
    )


def _readiness_from_broker(broker: Any) -> LivePipelineSmokeReadiness:
    qty = float(getattr(broker, "qty", 0.0) if hasattr(broker, "qty") else 0.0)
    return LivePipelineSmokeReadiness(
        broker_qty=qty,
        portfolio_qty=qty,
        projected_total_qty=qty,
        open_order_count=0,
        submit_unknown_count=0,
        recovery_required_count=0,
        fee_pending_count=0,
        active_fee_accounting_blocker=False,
        broker_qty_known=True,
        balance_source_stale=False,
        projection_converged=True,
    )


def _summary_for_step(
    *,
    side: str,
    target_exposure_krw: float,
    current_exposure_krw: float,
    qty: float,
    notional_krw: float,
    market: str,
    context: dict[str, object],
) -> ExecutionDecisionSummary:
    plan = ExecutionSubmitPlan(
        side=str(side).upper(),
        source="target_delta",
        authority="canonical_target_delta_sizing",
        final_action="REBALANCE_TO_TARGET",
        qty=float(qty),
        notional_krw=float(notional_krw),
        target_exposure_krw=float(target_exposure_krw),
        current_effective_exposure_krw=float(current_exposure_krw),
        delta_krw=float(notional_krw),
        submit_expected=True,
        pre_submit_proof_status="passed",
        block_reason="none",
        idempotency_key=f"{context['run_id']}:{context['step_index']}:{side}",
        pair=str(market),
        scope_key_hash="sha256:live_pipeline_smoke_scope",
        portfolio_target_hash="sha256:live_pipeline_smoke_portfolio_target",
        submit_authority_policy_hash="sha256:live_pipeline_smoke_submit_policy",
        extra_payload={
            "portfolio_target_authoritative": True,
            "portfolio_target_hash": "sha256:live_pipeline_smoke_portfolio_target",
            "allocation_decision_hash": "sha256:live_pipeline_smoke_allocation",
            "allocator_config_hash": "sha256:live_pipeline_smoke_allocator",
            "strategy_contribution_hash": "sha256:live_pipeline_smoke_contribution",
            "runtime_pair": str(market),
            "authoritative_pair": str(market),
            "operator_live_pipeline_smoke": True,
        },
    )
    return ExecutionDecisionSummary(
        raw_signal=str(side).upper(),
        final_signal=str(side).upper(),
        final_action="REBALANCE_TO_TARGET",
        submit_expected=True,
        pre_submit_proof_status="passed",
        block_reason="none",
        strategy_sell_candidate=None,
        residual_sell_candidate=None,
        target_exposure_krw=float(target_exposure_krw),
        current_effective_exposure_krw=float(current_exposure_krw),
        tracked_residual_exposure_krw=None,
        buy_delta_krw=float(notional_krw) if str(side).upper() == "BUY" else None,
        residual_live_sell_mode="telemetry",
        residual_buy_sizing_mode="telemetry",
        residual_submit_plan=None,
        buy_submit_plan=None,
        target_shadow_decision={
            "target_new_exposure_krw": float(target_exposure_krw),
            "target_current_exposure_krw": float(current_exposure_krw),
            "target_delta_notional_krw": float(notional_krw),
            "target_delta_side": str(side).upper(),
            "target_qty": float(qty if side == "BUY" else 0.0),
            "target_reference_price": 100_000_000.0,
            "target_origin": "operator_live_pipeline_smoke",
            "target_adoption_reason": "operator_authorized_pipeline_smoke",
        },
        target_submit_plan=plan,
        signal_flow={"primary_block_layer": "none", "primary_block_reason": "none"},
    )


def _record_smoke_decision(conn: Any, *, ts: int, side: str, context: dict[str, object]) -> int:
    return record_strategy_decision(
        conn,
        decision_ts=ts,
        strategy_name=OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME,
        signal=str(side).upper(),
        reason="operator_authorized_pipeline_smoke",
        candle_ts=ts,
        market_price=100_000_000.0,
        confidence=1.0,
        context=context,
        strategy_decision_projection_type="operator_live_pipeline_smoke",
        strategy_decisions_authority="operator_authorized_pipeline_smoke",
    )


def run_live_pipeline_smoke(
    *,
    conn: Any,
    broker: Any,
    cycles: int = LIVE_PIPELINE_SMOKE_CYCLES,
    max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS,
    max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    yes: bool = False,
    authority_path: str | None = None,
    confirm: str | None = None,
    execution_service: Any | None = None,
    readiness_provider: Callable[[], LivePipelineSmokeReadiness] | None = None,
    post_trade_reconcile: Callable[[], Any] | None = None,
    run_id: str | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    validate_live_pipeline_smoke_request(
        apply=True,
        yes=yes,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        authority_path=authority_path,
        confirm=confirm,
    )
    market = str(market or settings.PAIR).strip().upper()
    code_commit_sha = str(runtime_code_provenance().get("commit_sha") or "unavailable")
    authority = load_live_pipeline_smoke_authority(str(authority_path))
    authority.verify(
        now=datetime.now(timezone.utc),
        market=market,
        db_path=str(settings.DB_PATH),
        account_key=str(settings.BITHUMB_API_KEY),
        code_commit_sha=code_commit_sha,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
    )
    smoke_run_id = str(run_id or f"lps_{uuid.uuid4().hex[:12]}")
    validate_live_pipeline_smoke_start_preflight(
        cfg=settings,
        conn=conn,
        broker=broker,
        market=market,
    )
    authority.consume(
        consumed_at=datetime.now(timezone.utc),
        market=market,
        db_path=str(settings.DB_PATH),
        account_key=str(settings.BITHUMB_API_KEY),
        code_commit_sha=code_commit_sha,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        run_id=smoke_run_id,
    )

    provider = LivePipelineSmokeDecisionProvider(
        run_id=smoke_run_id,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
    )
    coordinator = ExecutionCoordinator(execution_engine_name="target_delta")
    service = execution_service or build_signal_execution_service(mode="live", broker=broker)
    if service is None:
        raise LivePipelineSmokeError("live_pipeline_smoke_execution_service_unavailable")
    readiness = readiness_provider or (lambda: readiness_from_snapshot(compute_runtime_readiness_snapshot(conn)))
    reconcile = post_trade_reconcile or (lambda: None)
    rounds: list[dict[str, Any]] = []
    flat_round: dict[str, Any] = {}
    orders_submitted = 0
    buy_submitted = 0
    sell_submitted = 0

    for step in range(max_orders):
        current = readiness()
        side = provider.next_side(current)
        if side == "STOP":
            break
        validate_live_pipeline_smoke_step_readiness(current, expected_side=side)
        context = provider.context_for_step(side=side)
        ts = int(time.time() * 1000) + step
        current_exposure = float(current.broker_qty) * 100_000_000.0
        target_exposure = provider.target_exposure_krw_for_side(side)
        qty = (
            float(max_notional_krw) / 100_000_000.0
            if side == "BUY"
            else float(current.broker_qty)
        )
        notional = float(max_notional_krw) if side == "BUY" else max(0.0, float(current_exposure))
        summary = _summary_for_step(
            side=side,
            target_exposure_krw=target_exposure,
            current_exposure_krw=current_exposure,
            qty=qty,
            notional_krw=notional,
            market=market,
            context=context,
        )
        context["execution_decision"] = summary.as_dict()
        decision_id = _record_smoke_decision(conn, ts=ts, side=side, context=context)
        conn.commit()
        def _submit_invoker(
            *,
            _side: str = side,
            _ts: int = ts,
            _decision_id: int = decision_id,
            _summary: ExecutionDecisionSummary = summary,
            _context: dict[str, object] = context,
        ) -> Any:
            return service.execute(
                build_signal_execution_request(
                    signal=_side,
                    ts=_ts,
                    market_price=100_000_000.0,
                    strategy_name=OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME,
                    decision_id=_decision_id,
                    decision_reason="operator_authorized_pipeline_smoke",
                    exit_rule_name=None,
                    execution_decision_summary=_summary,
                    decision_context=_context,
                    execution_plan_bundle=None,
                )
            )

        result = coordinator.execute_cycle(
            candle_ts=ts,
            decision_id=decision_id,
            signal=side,
            market_price=100_000_000.0,
            strategy_name=OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME,
            decision_reason="operator_authorized_pipeline_smoke",
            decision_context=context,
            execution_decision_summary=summary,
            execution_plan_bundle=None,
            execution_service=None,
            submit_invoker=_submit_invoker,
            post_trade_reconcile=reconcile,
            input_hash=None,
        )
        if not result.submitted or result.halt_transition:
            return _failure_payload(
                run_id=smoke_run_id,
                reason=result.planning_status,
                step=step,
                round_index=(step // 2) + 1,
                orders_submitted=orders_submitted,
            )
        after = readiness()
        try:
            validate_live_pipeline_smoke_step_readiness(after, expected_side=("SELL" if side == "BUY" else "BUY"))
        except LivePipelineSmokePreflightError as exc:
            return _failure_payload(
                run_id=smoke_run_id,
                reason=str(exc),
                step=step,
                round_index=(step // 2) + 1,
                orders_submitted=orders_submitted + 1,
            )
        trade = dict(result.trade or {})
        evidence = {
            "decision_id": decision_id,
            "client_order_id": trade.get("client_order_id"),
            "exchange_order_id": trade.get("exchange_order_id"),
            "submitted": True,
            "post_trade_reconciled": bool(result.post_trade_reconciled),
            "broker_qty_after": float(after.broker_qty),
            "filled_qty": float(trade.get("filled_qty") or qty),
        }
        if not result.post_trade_reconciled:
            return _failure_payload(
                run_id=smoke_run_id,
                reason="live_pipeline_smoke_post_trade_reconcile_failed",
                step=step,
                round_index=(step // 2) + 1,
                orders_submitted=orders_submitted + 1,
            )
        orders_submitted += 1
        if side == "BUY":
            if not after.in_position:
                return _failure_payload(run_id=smoke_run_id, reason="live_pipeline_smoke_buy_did_not_create_position", step=step, round_index=(step // 2) + 1, orders_submitted=orders_submitted)
            buy_submitted += 1
            flat_round = {"round": (step // 2) + 1, "buy": evidence}
        else:
            if not after.flat:
                return _failure_payload(run_id=smoke_run_id, reason="live_pipeline_smoke_sell_did_not_end_flat", step=step, round_index=(step // 2) + 1, orders_submitted=orders_submitted)
            sell_submitted += 1
            evidence["flat_after_sell"] = True
            flat_round["sell"] = evidence
            rounds.append(flat_round)
            flat_round = {}
        provider.mark_step_complete()

    final = readiness()
    if orders_submitted != max_orders or buy_submitted != cycles or sell_submitted != cycles or not final.flat:
        return _failure_payload(
            run_id=smoke_run_id,
            reason="live_pipeline_smoke_final_completion_criteria_failed",
            step=provider.step_index,
            round_index=(provider.step_index // 2) + 1,
            orders_submitted=orders_submitted,
        )
    return {
        "status": "passed",
        "execution_mode": "live_pipeline_smoke",
        "run_id": smoke_run_id,
        "cycles_requested": int(cycles),
        "orders_expected": int(max_orders),
        "orders_submitted": int(orders_submitted),
        "buy_submitted": int(buy_submitted),
        "sell_submitted": int(sell_submitted),
        "rounds": rounds,
        "final": {
            "broker_qty": float(final.broker_qty),
            "portfolio_qty": float(final.portfolio_qty),
            "projected_total_qty": float(final.projected_total_qty),
            "open_order_count": int(final.open_order_count),
            "submit_unknown_count": int(final.submit_unknown_count),
            "recovery_required_count": int(final.recovery_required_count),
        },
        "execution_mode_metadata": {
            "execution_mode": "live_pipeline_smoke",
            "candle_checkpoint_authority": "smoke_step_checkpoint",
            "market_reference_source": "latest_closed_candle_or_top_of_book",
            "normal_h74_strategy_performance_authority": False,
        },
    }


def _failure_payload(
    *,
    run_id: str,
    reason: str,
    step: int,
    round_index: int,
    orders_submitted: int,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "execution_mode": "live_pipeline_smoke",
        "run_id": run_id,
        "reason": str(reason),
        "failed_step": int(step),
        "failed_round": int(round_index),
        "orders_submitted": int(orders_submitted),
        "next_operator_action": "inspect health/audit and use flatten-position if exposure remains",
    }


def cmd_live_pipeline_smoke(
    *,
    plan: bool,
    apply: bool,
    yes: bool,
    cycles: int,
    max_orders: int,
    max_notional_krw: float,
    authority_path: str | None = None,
    confirm: str | None = None,
    json_output: bool = False,
) -> dict[str, Any]:
    if plan == apply:
        raise LivePipelineSmokeError("live_pipeline_smoke_requires_exactly_one_of_plan_or_apply")
    if plan:
        payload = build_live_pipeline_smoke_plan(
            cycles=cycles,
            max_orders=max_orders,
            max_notional_krw=max_notional_krw,
        )
    else:
        from .broker.bithumb import build_broker_with_auth_diagnostics

        conn = ensure_db()
        try:
            broker, _auth_diag = build_broker_with_auth_diagnostics(caller="live_pipeline_smoke")
            from .recovery import reconcile_with_broker

            payload = run_live_pipeline_smoke(
                conn=conn,
                broker=broker,
                cycles=cycles,
                max_orders=max_orders,
                max_notional_krw=max_notional_krw,
                yes=yes,
                authority_path=authority_path,
                confirm=confirm,
                post_trade_reconcile=lambda: reconcile_with_broker(broker),
            )
        finally:
            conn.close()
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def cmd_live_pipeline_smoke_authority(
    *,
    out: str,
    cycles: int,
    max_orders: int,
    max_notional_krw: float,
    expires_min: int,
) -> dict[str, Any]:
    from .live_pipeline_smoke_authority import write_live_pipeline_smoke_authority

    validate_live_pipeline_smoke_request(
        apply=False,
        yes=False,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        authority_path=None,
        confirm=None,
    )
    payload = write_live_pipeline_smoke_authority(
        out,
        cycles=cycles,
        max_orders=max_orders,
        max_notional_krw=max_notional_krw,
        expires_min=expires_min,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload
