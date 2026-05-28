from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bithumb_bot.market_regime import RegimeCoverageRow, RegimePerformanceRow

from .metrics import ResearchMetrics
from .metrics_contract import (
    ClosedTradeRecord,
    EquityPoint,
    MetricContractV2,
    PositionInterval,
)


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class BacktestResourceLimits:
    max_runtime_s_per_candidate_split: float | None = None
    max_decisions_retained: int | None = None
    max_trades: int | None = None
    max_equity_points_retained: int | None = None
    max_rss_mb: float | None = None


@dataclass(frozen=True)
class BacktestHeartbeatPolicy:
    interval_s: float | None = None
    bar_interval: int | None = None


@dataclass
class BacktestRunContext:
    experiment_id: str = ""
    candidate_id: str = ""
    scenario_id: str = ""
    scenario_index: int | None = None
    split_name: str = ""
    report_detail: str = "full"
    resource_limits: BacktestResourceLimits = field(default_factory=BacktestResourceLimits)
    heartbeat: BacktestHeartbeatPolicy = field(default_factory=BacktestHeartbeatPolicy)
    progress_callback: ProgressCallback | None = None
    audit_trace: Any | None = None
    approved_profile: dict[str, object] | None = None
    candidate_regime_policy: dict[str, object] | None = None
    candidate_regime_policy_drives_research_execution: bool = False
    policy_materialization_mode: str = "research_exploratory"
    started_at: float = field(default_factory=time.perf_counter)


class BacktestResourceLimitExceeded(RuntimeError):
    def __init__(self, reason: str, evidence: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


@dataclass(frozen=True)
class BacktestRun:
    metrics: ResearchMetrics
    trades: tuple[dict[str, object], ...]
    candle_count: int
    warnings: tuple[str, ...]
    regime_performance: tuple[RegimePerformanceRow, ...] = ()
    regime_coverage: tuple[RegimeCoverageRow, ...] = ()
    execution_event_summary: dict[str, object] | None = None
    decisions: tuple[dict[str, object], ...] = ()
    equity_curve: tuple[EquityPoint, ...] = ()
    position_intervals: tuple[PositionInterval, ...] = ()
    closed_trades: tuple[ClosedTradeRecord, ...] = ()
    metrics_v2: MetricContractV2 | None = None
    resource_usage: dict[str, object] | None = None
    strategy_diagnostics: dict[str, object] | None = None
    retained_detail_summary: dict[str, object] | None = None
    audit_trace_index: dict[str, object] | None = None
