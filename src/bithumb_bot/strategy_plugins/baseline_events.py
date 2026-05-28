from __future__ import annotations

from typing import Any

from bithumb_bot.research.dataset_snapshot import DatasetSnapshot
from bithumb_bot.research.decision_event import ResearchDecisionEvent
from bithumb_bot.research.execution_timing import candle_close_ts
from bithumb_bot.research.experiment_manifest import ExecutionTimingPolicy, PortfolioPolicy, legacy_research_portfolio_policy
from bithumb_bot.research.strategy_spec import (
    BUY_AND_HOLD_BASELINE_SPEC,
    NOOP_BASELINE_SPEC,
)


def build_noop_baseline_events(
    *,
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    execution_timing_policy: ExecutionTimingPolicy,
    portfolio_policy: PortfolioPolicy,
    context: Any | None = None,
) -> tuple[ResearchDecisionEvent, ...]:
    del fee_rate, slippage_bps, portfolio_policy, context
    start_index = max(0, int(parameter_values.get("NOOP_DECISION_START_INDEX", 0)))
    decision_reason = str(parameter_values.get("NOOP_DECISION_REASON") or "noop_baseline_hold")
    events: list[ResearchDecisionEvent] = []
    for index, candle in enumerate(dataset.candles):
        if index < start_index:
            continue
        mark_boundary_ts = candle_close_ts(candle, interval=dataset.interval)
        decision_boundary_ts = mark_boundary_ts + int(execution_timing_policy.decision_guard_ms)
        feature_snapshot = {
            "candle_index": int(index),
            "close": float(candle.close),
            "start_index": int(start_index),
        }
        events.append(
            ResearchDecisionEvent(
                candle_ts=int(candle.ts),
                decision_ts=int(decision_boundary_ts),
                strategy_name=NOOP_BASELINE_SPEC.strategy_name,
                strategy_version=NOOP_BASELINE_SPEC.strategy_version,
                raw_signal="HOLD",
                final_signal="HOLD",
                reason=decision_reason,
                feature_snapshot=feature_snapshot,
                strategy_diagnostics={
                    "schema_version": 1,
                    "hold_decision_count": int(len(events) + 1),
                    "start_index": int(start_index),
                },
                entry_signal="HOLD",
                exit_signal="HOLD",
                extra_payload={"regime_snapshot": {"composite_regime": "not_evaluated"}},
            )
        )
    return tuple(events)


def build_buy_and_hold_baseline_events(
    *,
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    execution_timing_policy: ExecutionTimingPolicy,
    portfolio_policy: PortfolioPolicy,
    context: Any | None = None,
) -> tuple[ResearchDecisionEvent, ...]:
    del fee_rate, slippage_bps, context
    buy_index = max(0, int(parameter_values.get("BUY_HOLD_BUY_INDEX", 0)))
    decision_reason = str(parameter_values.get("BUY_HOLD_DECISION_REASON") or "buy_and_hold_architecture_canary")
    policy = portfolio_policy or legacy_research_portfolio_policy()
    events: list[ResearchDecisionEvent] = []
    for index, candle in enumerate(dataset.candles):
        action = "BUY" if index == buy_index else "HOLD"
        decision_ts = candle_close_ts(candle, interval=dataset.interval) + int(execution_timing_policy.decision_guard_ms)
        feature_snapshot = {
            "candle_index": int(index),
            "buy_index": int(buy_index),
            "close": float(candle.close),
        }
        events.append(
            ResearchDecisionEvent(
                candle_ts=int(candle.ts),
                decision_ts=int(decision_ts),
                strategy_name=BUY_AND_HOLD_BASELINE_SPEC.strategy_name,
                strategy_version=BUY_AND_HOLD_BASELINE_SPEC.strategy_version,
                raw_signal=action,
                final_signal=action,
                reason=decision_reason if action == "BUY" else "buy_and_hold_after_entry_hold",
                feature_snapshot=feature_snapshot,
                strategy_diagnostics={
                    "schema_version": 1,
                    "buy_index": int(buy_index),
                    "candle_index": int(index),
                    "emitted_buy_intent": action == "BUY",
                },
                entry_signal=action if action == "BUY" else "HOLD",
                exit_signal="HOLD",
                order_intent=(
                    {
                        "side": "BUY",
                        "sizing": "portfolio_policy_fractional_cash",
                        "buy_fraction": float(policy.position_sizing.buy_fraction),
                    }
                    if action == "BUY"
                    else None
                ),
            )
        )
    return tuple(events)
