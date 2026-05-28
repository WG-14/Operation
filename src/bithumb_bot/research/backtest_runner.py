from __future__ import annotations

from typing import Any

from .backtest_types import BacktestRun, BacktestRunContext
from .dataset_snapshot import DatasetSnapshot
from .execution_model import ExecutionModel
from .experiment_manifest import ExecutionTimingPolicy, PortfolioPolicy, legacy_research_portfolio_policy
from .strategy_spec import materialize_strategy_parameters


def run_plugin_backtest(
    *,
    plugin: Any,
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    parameter_stability_score: float | None = None,
    execution_model: ExecutionModel | None = None,
    execution_timing_policy: ExecutionTimingPolicy | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    context: BacktestRunContext | None = None,
) -> BacktestRun:
    event_builder = getattr(plugin, "research_event_builder", None)
    if event_builder is None:
        raise ValueError(f"research_event_builder_missing:{plugin.name}")
    effective_parameters = materialize_strategy_parameters(
        plugin.name,
        parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )
    if plugin.name == "sma_with_filter":
        # Historical research compatibility: exploratory SMA tests intentionally
        # ran raw crosses unless a filter was explicitly supplied. Strict
        # promotion/runtime materialization remains owned by the assembly layer.
        legacy_disabled_filter_defaults = {
            "SMA_FILTER_GAP_MIN_RATIO": 0.0,
            "SMA_FILTER_VOL_MIN_RANGE_RATIO": 0.0,
            "SMA_FILTER_OVEREXT_MAX_RETURN_RATIO": 0.0,
            "SMA_COST_EDGE_ENABLED": False,
            "SMA_MARKET_REGIME_ENABLED": False,
        }
        for key, value in legacy_disabled_filter_defaults.items():
            if key not in parameter_values:
                effective_parameters[key] = value
    timing_policy = execution_timing_policy or ExecutionTimingPolicy()
    policy = portfolio_policy or legacy_research_portfolio_policy()
    decision_events = event_builder(
        dataset=dataset,
        parameter_values=effective_parameters,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        execution_timing_policy=timing_policy,
        portfolio_policy=policy,
        context=context,
    )
    if plugin.name == "sma_with_filter" and not decision_events:
        return _empty_plugin_backtest_result(
            plugin=plugin,
            dataset=dataset,
            parameter_stability_score=parameter_stability_score,
            portfolio_policy=policy,
            context=context,
        )
    from . import backtest_kernel

    return backtest_kernel.run_decision_event_backtest(
        dataset=dataset,
        strategy_name=plugin.name,
        parameter_values=effective_parameters,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        decision_events=tuple(decision_events),
        parameter_stability_score=parameter_stability_score,
        execution_model=execution_model,
        execution_timing_policy=timing_policy,
        portfolio_policy=policy,
        context=context,
    )


def _empty_plugin_backtest_result(
    *,
    plugin: Any,
    dataset: DatasetSnapshot,
    parameter_stability_score: float | None,
    portfolio_policy: PortfolioPolicy,
    context: BacktestRunContext | None,
) -> BacktestRun:
    from . import backtest_support as support

    run_context = context or BacktestRunContext(report_detail="full")
    starting_cash = float(portfolio_policy.starting_cash_krw)
    initial_qty = float(portfolio_policy.initial_position_qty)
    accumulator = support.BacktestAccumulator(
        context=run_context,
        total_candles=len(dataset.candles),
        diagnostics_namespace=str(plugin.diagnostics_namespace),
    )
    audit_trace_index = support.complete_audit_trace(run_context, status="completed")
    return BacktestRun(
        metrics=support.empty_metrics(parameter_stability_score),
        metrics_v2=support.empty_metrics_v2(
            starting_cash=starting_cash,
            initial_position_qty=initial_qty,
        ),
        trades=(),
        candle_count=len(dataset.candles),
        warnings=("not_enough_candles",),
        regime_performance=(),
        regime_coverage=(),
        execution_event_summary=support.empty_execution_event_summary(),
        decisions=(),
        equity_curve=(),
        resource_usage=accumulator.resource_usage(candles_processed=len(dataset.candles)),
        strategy_diagnostics=accumulator.strategy_diagnostics(trades=[]),
        retained_detail_summary=support.retained_detail_summary(
            accumulator,
            retained_regime_snapshot_count=0,
        ),
        audit_trace_index=audit_trace_index,
    )
