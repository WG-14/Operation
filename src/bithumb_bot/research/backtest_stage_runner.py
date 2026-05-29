from __future__ import annotations

from typing import Any

from . import backtest_support as support
from bithumb_bot.canonical_decision import canonical_payload_hash

from .audit_trace_recorder import AuditTraceRecorder
from .backtest_result_assembler import BacktestResultAssembler
from .backtest_stages import ReplayTick
from .decision_payload import DecisionPayloadBuilder
from .execution_simulator_stage import blocked_execution_evidence
from .execution_model import FixedBpsExecutionModel
from .execution_timing import candle_close_ts
from .experiment_manifest import ExecutionTimingPolicy, legacy_research_portfolio_policy
from .metrics_contract import EquityPoint
from .portfolio_ledger import PortfolioLedger
from .stage_trace_recorder import StageTraceRecorder
from .strategy_spec import exit_policy_from_parameters, exit_policy_hash, strategy_spec_for_name


def run_stage_owned_decision_event_backtest(
    *,
    dataset: Any,
    strategy_name: str,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    decision_events: tuple[Any, ...],
    parameter_stability_score: float | None = None,
    execution_model: Any | None = None,
    execution_timing_policy: Any | None = None,
    portfolio_policy: Any | None = None,
    context: Any | None = None,
    prepared_ticks: tuple[ReplayTick, ...] | None = None,
    prepared_ledger: PortfolioLedger | None = None,
    strategy_evaluator: Any | None = None,
    risk_gate: Any | None = None,
    execution_simulator: Any | None = None,
    metrics_collector: Any | None = None,
    experiment_recorder: Any | None = None,
) -> Any:
    from .backtest_pipeline import BacktestPipelineState, DefaultMarketReplayClock
    from .strategy_registry import resolve_research_strategy_plugin

    strategy_plugin = resolve_research_strategy_plugin(strategy_name)
    strategy_spec = strategy_spec_for_name(strategy_name)
    active_exit_policy = exit_policy_from_parameters(strategy_name, parameter_values)
    active_exit_policy_hash = exit_policy_hash(active_exit_policy)
    payload_builder = DecisionPayloadBuilder()
    audit_recorder = AuditTraceRecorder()
    trace_recorder = StageTraceRecorder()
    result_assembler = BacktestResultAssembler()
    candles = dataset.candles
    run_context = context or support.BacktestRunContext(report_detail="full")
    timing_policy = execution_timing_policy or ExecutionTimingPolicy()
    policy = portfolio_policy or legacy_research_portfolio_policy()
    model = execution_model or FixedBpsExecutionModel(fee_rate=fee_rate, slippage_bps=slippage_bps)
    starting_cash = float(policy.starting_cash_krw)
    ledger = prepared_ledger or PortfolioLedger.create(
        starting_cash=starting_cash,
        initial_position_qty=float(policy.initial_position_qty),
    )
    buy_fraction = float(policy.position_sizing.buy_fraction)
    accumulator = support.BacktestAccumulator(
        context=run_context,
        total_candles=len(candles),
        diagnostics_namespace=strategy_plugin.diagnostics_namespace,
    )
    if not candles:
        return result_assembler.empty_run(
            run_context=run_context,
            accumulator=accumulator,
            starting_cash=starting_cash,
            initial_position_qty=float(policy.initial_position_qty),
            parameter_stability_score=parameter_stability_score,
        )

    if prepared_ticks is None:
        prepared_ticks = DefaultMarketReplayClock().run(
            BacktestPipelineState(
                dataset=dataset,
                strategy_name=strategy_name,
                parameter_values=parameter_values,
                fee_rate=fee_rate,
                slippage_bps=slippage_bps,
                decision_events=decision_events,
                parameter_stability_score=parameter_stability_score,
                execution_model=execution_model,
                execution_timing_policy=timing_policy,
                portfolio_policy=policy,
                context=run_context,
            )
        ).ticks

    dataset_content_hash = dataset.content_hash()
    decisions: list[dict[str, object]] = []
    warnings: list[str] = []
    regime_snapshots: list[dict[str, object]] = []
    regime_coverage_accumulator = support.RegimeCoverageAccumulator()

    first = candles[0]
    first_ts = candle_close_ts(first, interval=dataset.interval)
    retain_initial_equity = accumulator.retain_equity_point()
    if retain_initial_equity:
        ledger.equity_curve.append(
            EquityPoint(ts=first_ts, equity=starting_cash, cash=ledger.cash, asset_qty=ledger.qty)
        )
    accumulator.update_equity(retained=retain_initial_equity, ts=first_ts, asset_qty=ledger.qty)
    audit_recorder.record_equity_mark(
        run_context,
        ts=first_ts,
        equity=starting_cash,
        cash=ledger.cash,
        asset_qty=ledger.qty,
    )

    for event_number, tick in enumerate(prepared_ticks, start=1):
        event = tick.event
        candle = tick.candle
        mark_boundary_ts = candle_close_ts(candle, interval=dataset.interval)
        decision_boundary_ts = int(event.decision_ts)
        tick_state = ledger.begin_tick(
            mark_boundary_ts=mark_boundary_ts,
            decision_boundary_ts=decision_boundary_ts,
            candle_ts=int(candle.ts),
            close=float(candle.close),
        )
        mark_cash = tick_state.mark_cash
        mark_qty = tick_state.mark_qty
        sellable_qty = tick_state.sellable_qty
        event_extra = event.extra_payload if isinstance(event.extra_payload, dict) else {}
        regime_snapshot = dict(
            event_extra.get("regime_snapshot")
            or {"composite_regime": "strategy_neutral_not_evaluated"}
        )
        regime_coverage_accumulator.update(regime_snapshot)
        if accumulator.retain_full_detail():
            regime_snapshots.append(regime_snapshot)

        policy_position = ledger.snapshot_for_policy(
            candle_ts=int(candle.ts),
            market_price=float(candle.close),
        )
        replay_tick_hash = canonical_payload_hash(
            {
                "candle_ts": int(tick.candle_ts),
                "decision_ts": int(tick.decision_ts),
                "raw_signal": event.raw_signal,
                "final_signal": event.final_signal,
                "reason": event.reason,
            }
        )
        position_snapshot_hash = canonical_payload_hash(
            policy_position.as_dict() if hasattr(policy_position, "as_dict") else vars(policy_position)
        )
        strategy_envelope = strategy_evaluator.evaluate(
            tick,
            policy_position,
            {
                "dataset": dataset,
                "strategy_name": strategy_name,
                "parameter_values": parameter_values,
                "fee_rate": fee_rate,
                "slippage_bps": slippage_bps,
                "active_exit_policy": active_exit_policy,
                "buy_fraction": buy_fraction,
                "run_context": run_context,
            },
        )
        strategy_decision_hash = canonical_payload_hash(
            {
                "replay_fingerprint_hash": strategy_envelope.replay_fingerprint_hash,
                "compatibility_fallback": strategy_envelope.compatibility_fallback,
                "unsupported_reason": strategy_envelope.unsupported_reason,
                "decision_hash": (
                    getattr(strategy_envelope.decision, "policy_decision_hash", "")
                    if strategy_envelope.decision is not None
                    else ""
                ),
            }
        )
        trace_recorder.record_strategy(
            replay_tick_hash=replay_tick_hash,
            position_snapshot_hash=position_snapshot_hash,
            strategy_decision_hash=strategy_decision_hash,
            compatibility_fallback=bool(strategy_envelope.compatibility_fallback),
            unsupported_reason=strategy_envelope.unsupported_reason,
            recommended_next_action=strategy_envelope.recommended_next_action,
        )
        policy_decision = strategy_envelope.decision
        risk_decision = risk_gate.evaluate(
            policy_decision,
            policy_position,
            {
                "candle_ts": int(candle.ts),
                "close": float(candle.close),
            },
            {
                "qty": ledger.qty,
                **ledger.portfolio_snapshot(tick_state),
            },
            {
                "strategy_plugin": strategy_plugin,
                "event": event,
                "active_exit_policy": active_exit_policy,
                "parameter_values": parameter_values,
                "fee_rate": fee_rate,
                "strategy_envelope": strategy_envelope,
            },
        )
        risk_gate_hash = risk_decision.evidence_hash
        trace_recorder.record_risk(
            input_hash=strategy_decision_hash,
            risk_gate_hash=risk_gate_hash,
            reason_code=risk_decision.reason_code,
        )
        action = risk_decision.final_signal
        decision_payload = payload_builder.build(
            dataset=dataset,
            dataset_content_hash=dataset_content_hash,
            parameter_values=parameter_values,
            strategy_plugin=strategy_plugin,
            strategy_spec=strategy_spec,
            exit_policy=active_exit_policy,
            exit_policy_hash=active_exit_policy_hash,
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
            timing_policy=timing_policy,
            portfolio_policy=policy,
            event=event,
            decision_boundary_ts=decision_boundary_ts,
            strategy_envelope=strategy_envelope,
            risk_decision=risk_decision,
            policy_position=policy_position,
            policy_decision=policy_decision,
            regime_snapshot=regime_snapshot,
            qty=ledger.qty,
            sellable_qty=sellable_qty,
        )
        if action in {"BUY", "SELL"}:
            outcome = execution_simulator.execute(
                dataset=dataset,
                candle=candle,
                candle_index=int(tick.candle_index),
                event=event,
                ledger=ledger,
                timing_policy=timing_policy,
                execution_model=model,
                fee_rate=fee_rate,
                strategy_name=strategy_plugin.name,
                action=action,
                decision_reason=risk_decision.reason_code,
                regime_snapshot=regime_snapshot,
                decision_hash=str(decision_payload.get("replay_fingerprint_hash") or ""),
                sellable_qty=sellable_qty,
                buy_fraction=buy_fraction,
                promotion_grade_policy_required=bool(
                    strategy_envelope.provenance.get("promotion_grade_policy_required")
                ),
                allow_execution_compatibility_fallback=bool(
                    policy_decision is None
                    and not strategy_envelope.unsupported_reason
                    and (
                        strategy_plugin.research_policy_decision_builder is None
                        or bool(strategy_envelope.provenance.get("allows_legacy_event_first_exit_policy"))
                    )
                ),
                policy_drives_execution=True,
                policy_decision=policy_decision,
                exit_rule=risk_decision.exit_rule,
                exit_reason=risk_decision.exit_reason,
            )
            decision_payload.update(dict(outcome.evidence))
            warnings.extend(outcome.warnings)
            application = ledger.apply_execution_outcome(
                outcome,
                mark_boundary_ts=mark_boundary_ts,
                mark_cash=mark_cash,
                mark_qty=mark_qty,
            )
            mark_cash = application.mark_cash
            mark_qty = application.mark_qty
            if application.trade_recorded:
                audit_recorder.record_execution(run_context, ledger.trade_ledger[-1])
                ledger.apply_pending_fills(decision_boundary_ts)
            execution_plan_hash = canonical_payload_hash(dict(outcome.evidence))
            fill_hash = canonical_payload_hash(
                outcome.fill.as_dict() if outcome.fill is not None and hasattr(outcome.fill, "as_dict") else {}
            )
        else:
            blocked_evidence = blocked_execution_evidence(risk_decision.reason_code)
            decision_payload.update(blocked_evidence)
            execution_plan_hash = canonical_payload_hash(blocked_evidence)
            fill_hash = canonical_payload_hash({})
        trace_recorder.record_execution(
            input_hash=risk_gate_hash,
            execution_plan_hash=execution_plan_hash,
            fill_hash=fill_hash,
            reason_code=str(decision_payload.get("execution_plan_reason_code") or risk_decision.reason_code),
        )
        retain_decision = accumulator.retain_decision()
        if retain_decision:
            decisions.append(decision_payload)
        accumulator.update_decision(decision_payload, retained=retain_decision)
        audit_recorder.record_decision(run_context, decision_payload)

        retain_equity = accumulator.retain_equity_point()
        ledger.mark_tick_equity(
            ts=mark_boundary_ts,
            mark_price=float(candle.close),
            cash=mark_cash,
            qty=mark_qty,
        )
        trace_recorder.record_ledger_and_equity(
            execution_plan_hash=execution_plan_hash,
            ledger_snapshot=ledger.portfolio_snapshot(),
            mark_boundary_ts=mark_boundary_ts,
            mark_cash=mark_cash,
            mark_qty=mark_qty,
            mark_price=float(candle.close),
        )
        if not retain_equity and ledger.equity_curve:
            ledger.equity_curve.pop()
        accumulator.update_equity(retained=retain_equity, ts=mark_boundary_ts, asset_qty=mark_qty)
        audit_recorder.record_equity_mark(
            run_context,
            ts=mark_boundary_ts,
            equity=mark_cash + mark_qty * float(candle.close),
            cash=mark_cash,
            asset_qty=mark_qty,
        )
        trace_recorder.flush_latest(
            count=5,
            metrics_collector=metrics_collector,
            experiment_recorder=experiment_recorder,
            event_number=event_number,
        )
        accumulator.maybe_emit_heartbeat(event_number)
        accumulator.check_limits(candles_processed=event_number, trades=ledger.trade_ledger)

    return result_assembler.assemble(
        dataset=dataset,
        candles=tuple(candles),
        decision_events=decision_events,
        ledger=ledger,
        accumulator=accumulator,
        run_context=run_context,
        starting_cash=starting_cash,
        parameter_stability_score=parameter_stability_score,
        regime_snapshots=regime_snapshots,
        regime_coverage_accumulator=regime_coverage_accumulator,
        decisions=decisions,
        warnings=warnings,
        stage_trace_records=[trace.as_dict() for trace in trace_recorder.traces],
    )
