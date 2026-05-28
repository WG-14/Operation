from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from bithumb_bot.market_regime import classify_market_regime_from_arrays
from bithumb_bot.market_regime.thresholds import MarketRegimeThresholds
from bithumb_bot.research.dataset_snapshot import DatasetSnapshot
from bithumb_bot.research.decision_event import ResearchDecisionEvent
from bithumb_bot.research.execution_timing import candle_close_ts
from bithumb_bot.research.experiment_manifest import ExecutionTimingPolicy
from bithumb_bot.research.strategy_spec import strategy_spec_for_name

@dataclass(frozen=True)
class SmaWithFilterDecisionAdapter:
    parameter_values: dict[str, Any]
    fee_rate: float
    slippage_bps: float
    timing_policy: ExecutionTimingPolicy
    strategy_name: str = "sma_with_filter"

    def build_events(self, dataset: DatasetSnapshot) -> tuple[ResearchDecisionEvent, ...]:
        # Compatibility serialization layer only. The backtest kernel must
        # re-evaluate sma_with_filter through StrategyDecisionV2 with the
        # simulated position before treating final action fields as authority.
        short_n = int(self.parameter_values.get("SMA_SHORT", self.parameter_values.get("short_n", 0)))
        long_n = int(self.parameter_values.get("SMA_LONG", self.parameter_values.get("long_n", 0)))
        if short_n <= 0 or long_n <= 0 or short_n >= long_n:
            raise ValueError("SMA_SHORT must be smaller than SMA_LONG")

        candles = dataset.candles
        if len(candles) < long_n + 2:
            return ()

        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]
        volumes = [candle.volume for candle in candles]
        short_sma_values = _rolling_sma_values(closes, short_n)
        long_sma_values = _rolling_sma_values(closes, long_n)
        min_gap = float(
            self.parameter_values.get(
                "SMA_FILTER_GAP_MIN_RATIO",
                self.parameter_values.get("strategy_min_expected_edge_ratio", 0.0),
            )
        )
        min_range = float(self.parameter_values.get("SMA_FILTER_VOL_MIN_RANGE_RATIO", 0.0))
        volatility_window = max(1, int(self.parameter_values.get("SMA_FILTER_VOL_WINDOW", 10)))
        volume_window = max(1, int(self.parameter_values.get("SMA_FILTER_VOLUME_WINDOW", 10)))
        liquidity_window = max(1, int(self.parameter_values.get("SMA_FILTER_LIQUIDITY_WINDOW", 10)))
        overextended_lookback = max(1, int(self.parameter_values.get("SMA_FILTER_OVEREXT_LOOKBACK", 3)))
        overextended_max_return_ratio = float(self.parameter_values.get("SMA_FILTER_OVEREXT_MAX_RETURN_RATIO", 0.0))
        close_volatility_ratios = _rolling_close_range_ratios(closes, volatility_window)
        overextended_ratios = _overextended_return_ratios(closes, overextended_lookback)
        thresholds = MarketRegimeThresholds(
            min_trend_strength_ratio=max(0.0, min_gap),
            low_volatility_ratio=max(0.0, min_range),
        )
        strategy_spec = strategy_spec_for_name(self.strategy_name)
        events: list[ResearchDecisionEvent] = []
        prev_above: bool | None = None
        for index in range(long_n, len(candles)):
            candle = candles[index]
            prev_short = short_sma_values[index]
            prev_long = long_sma_values[index]
            curr_short = short_sma_values[index + 1]
            curr_long = long_sma_values[index + 1]
            if prev_short is None or prev_long is None or curr_short is None or curr_long is None:
                continue
            above = curr_short > curr_long
            regime_snapshot = classify_market_regime_from_arrays(
                closes=closes,
                highs=highs,
                lows=lows,
                volumes=volumes,
                index=index,
                short_sma=curr_short,
                long_sma=curr_long,
                volatility_window=volatility_window,
                volume_window=volume_window,
                liquidity_window=liquidity_window,
                thresholds=thresholds,
                overextended_lookback=overextended_lookback,
                overextended_max_return_ratio=overextended_max_return_ratio,
            ).as_dict()
            gap_ratio = abs((curr_short - curr_long) / curr_long) if curr_long != 0.0 else 0.0
            feature_snapshot = _feature_snapshot(
                short_sma=curr_short,
                long_sma=curr_long,
                gap_ratio=gap_ratio,
                range_ratio=close_volatility_ratios[index],
                index=index,
            )
            decision_ts = candle_close_ts(candle, interval=dataset.interval) + int(self.timing_policy.decision_guard_ms)
            events.append(
                ResearchDecisionEvent(
                    candle_ts=int(candle.ts),
                    decision_ts=int(decision_ts),
                    strategy_name=self.strategy_name,
                    strategy_version=strategy_spec.strategy_version,
                    raw_signal="HOLD",
                    final_signal="HOLD",
                    reason="research_event_adapter_non_authoritative",
                    feature_snapshot=feature_snapshot,
                    strategy_diagnostics={
                        "schema_version": 1,
                        "adapter": "SmaWithFilterDecisionAdapter",
                        "candle_index": int(index),
                        "authority": "historical_feature_serialization_only",
                    },
                    entry_signal="HOLD",
                    exit_signal="HOLD",
                    blocked_filters=(),
                    order_intent=None,
                    exit_intent={
                        "mode": "evaluate_exit_policy",
                        "base_signal": "HOLD",
                        "base_reason": "research_event_adapter_non_authoritative",
                    },
                    extra_payload={
                        "adapter": "SmaWithFilterDecisionAdapter",
                        "index": int(index),
                        "processed_count": int(index - long_n + 1),
                        "prev_above": prev_above,
                        "above": above,
                        "prev_s": float(prev_short),
                        "prev_l": float(prev_long),
                        "curr_s": float(curr_short),
                        "curr_l": float(curr_long),
                        "min_gap_ratio": float(min_gap),
                        "volatility_ratio": float(close_volatility_ratios[index]),
                        "overextended_ratio": float(overextended_ratios[index]),
                        "regime_snapshot": regime_snapshot,
                        "non_authoritative_event_adapter": True,
                    },
                )
            )
            prev_above = above
        return tuple(events)


def build_sma_with_filter_research_events(
    *,
    dataset: DatasetSnapshot,
    parameter_values: dict[str, Any],
    fee_rate: float,
    slippage_bps: float,
    execution_timing_policy: ExecutionTimingPolicy,
    portfolio_policy: Any | None = None,
    context: Any | None = None,
) -> tuple[ResearchDecisionEvent, ...]:
    del portfolio_policy, context
    return SmaWithFilterDecisionAdapter(
        parameter_values=parameter_values,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        timing_policy=execution_timing_policy,
    ).build_events(dataset)


def _sma(values: list[float], n: int, end: int) -> float:
    return sum(values[end - n : end]) / n


def _feature_snapshot(
    *,
    short_sma: float,
    long_sma: float,
    gap_ratio: float,
    range_ratio: float,
    index: int,
) -> dict[str, object]:
    return {
        "short_sma": float(short_sma),
        "long_sma": float(long_sma),
        "gap_ratio": float(gap_ratio),
        "range_ratio": float(range_ratio),
        "candle_index": int(index),
    }


def _rolling_sma_values(values: list[float], n: int) -> list[float | None]:
    window = int(n)
    out: list[float | None] = [None] * (len(values) + 1)
    if window <= 0 or len(values) < window:
        return out
    rolling_sum = sum(values[:window])
    out[window] = rolling_sum / window
    for end in range(window + 1, len(values) + 1):
        rolling_sum += values[end - 1]
        rolling_sum -= values[end - window - 1]
        out[end] = rolling_sum / window
    return out


def _rolling_close_range_ratios(values: list[float], window: int) -> list[float]:
    window = max(1, int(window))
    out: list[float] = [0.0] * len(values)
    min_indexes: deque[int] = deque()
    max_indexes: deque[int] = deque()
    rolling_sum = 0.0
    for index, value in enumerate(values):
        value = float(value)
        rolling_sum += value
        stale_before = index - window + 1
        if index >= window:
            rolling_sum -= float(values[index - window])
        while min_indexes and min_indexes[0] < stale_before:
            min_indexes.popleft()
        while max_indexes and max_indexes[0] < stale_before:
            max_indexes.popleft()
        while min_indexes and float(values[min_indexes[-1]]) >= value:
            min_indexes.pop()
        while max_indexes and float(values[max_indexes[-1]]) <= value:
            max_indexes.pop()
        min_indexes.append(index)
        max_indexes.append(index)
        count = min(window, index + 1)
        mean = rolling_sum / count if count > 0 else 0.0
        out[index] = (
            ((float(values[max_indexes[0]]) - float(values[min_indexes[0]])) / mean)
            if mean != 0.0 and min_indexes and max_indexes
            else 0.0
        )
    return out


def _overextended_return_ratios(values: list[float], lookback: int) -> list[float]:
    lookback = max(1, int(lookback))
    out: list[float] = [0.0] * len(values)
    for index, value in enumerate(values):
        if index < lookback:
            continue
        base = float(values[index - lookback])
        out[index] = abs((float(value) - base) / base) if base != 0.0 else 0.0
    return out
