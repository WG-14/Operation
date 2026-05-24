from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .decision_event import ResearchDecisionEvent

if TYPE_CHECKING:
    from .backtest_engine import BacktestRun, BacktestRunContext
    from .dataset_snapshot import DatasetSnapshot
    from .execution_model import ExecutionModel
    from .experiment_manifest import ExecutionTimingPolicy, PortfolioPolicy


@dataclass(frozen=True)
class BacktestKernel:
    """Stable common-kernel API for decision-event backtests.

    Transitional boundary: the public API lives in this module. The helper-heavy
    implementation is still hosted privately in ``backtest_engine`` until that
    helper graph can be split without duplicating execution/accounting logic.
    """

    def run(
        self,
        *,
        dataset: DatasetSnapshot,
        strategy_name: str,
        parameter_values: dict[str, Any],
        fee_rate: float,
        slippage_bps: float,
        decision_events: tuple[ResearchDecisionEvent, ...],
        parameter_stability_score: float | None = None,
        execution_model: ExecutionModel | None = None,
        execution_timing_policy: ExecutionTimingPolicy | None = None,
        portfolio_policy: PortfolioPolicy | None = None,
        context: BacktestRunContext | None = None,
    ) -> BacktestRun:
        return run_decision_event_backtest(
            dataset=dataset,
            strategy_name=strategy_name,
            parameter_values=parameter_values,
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
            decision_events=decision_events,
            parameter_stability_score=parameter_stability_score,
            execution_model=execution_model,
            execution_timing_policy=execution_timing_policy,
            portfolio_policy=portfolio_policy,
            context=context,
        )


def run_decision_event_backtest(
    *,
    dataset: DatasetSnapshot,
    strategy_name: str,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    decision_events: tuple[ResearchDecisionEvent, ...],
    parameter_stability_score: float | None = None,
    execution_model: ExecutionModel | None = None,
    execution_timing_policy: ExecutionTimingPolicy | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    context: BacktestRunContext | None = None,
) -> BacktestRun:
    # Transitional implementation boundary: public call sites enter here, while
    # the single implementation remains private in backtest_engine until the
    # surrounding helper graph can be split without behavior drift.
    from .backtest_engine import _run_decision_event_backtest_impl

    return _run_decision_event_backtest_impl(
        dataset=dataset,
        strategy_name=strategy_name,
        parameter_values=parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        decision_events=decision_events,
        parameter_stability_score=parameter_stability_score,
        execution_model=execution_model,
        execution_timing_policy=execution_timing_policy,
        portfolio_policy=portfolio_policy,
        context=context,
    )
