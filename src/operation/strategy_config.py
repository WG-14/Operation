from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .config import settings
from .operation_strategy.spec import SMA_WITH_FILTER_SPEC


@dataclass(frozen=True)
class SmaStrategyConfig:
    short_n: int
    long_n: int
    pair: str
    interval: str
    exit_rule_names: tuple[str, ...]
    exit_stop_loss_ratio: float
    exit_max_holding_min: int
    exit_min_take_profit_ratio: float
    exit_small_loss_tolerance_ratio: float
    slippage_bps: float
    live_fee_rate_estimate: float
    entry_edge_buffer_ratio: float
    strategy_min_expected_edge_ratio: float
    buy_fraction: float
    max_order_krw: float
    candidate_regime_policy: dict[str, object] | None = None


def normalize_exit_rule_names(raw: str | Iterable[object]) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = raw.split(",")
    else:
        values = raw
    return tuple(str(token).strip().lower() for token in values if str(token).strip())


def _sma_default(name: str) -> object:
    if name == "SMA_SHORT":
        return 7
    if name == "SMA_LONG":
        return 30
    return SMA_WITH_FILTER_SPEC.default_parameters[name]


def _sma_env_value(name: str) -> str | None:
    configured = getattr(settings, name, None)
    if configured is not None:
        return str(configured)
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def _sma_int(name: str) -> int:
    raw = _sma_env_value(name)
    if raw is None:
        return int(_sma_default(name))
    return int(raw)


def sma_strategy_config_from_settings(
    *,
    short_n: int | None = None,
    long_n: int | None = None,
) -> SmaStrategyConfig:
    return SmaStrategyConfig(
        short_n=int(_sma_int("SMA_SHORT") if short_n is None else short_n),
        long_n=int(_sma_int("SMA_LONG") if long_n is None else long_n),
        pair=str(settings.PAIR),
        interval=str(settings.INTERVAL),
        exit_rule_names=normalize_exit_rule_names(settings.STRATEGY_EXIT_RULES),
        exit_stop_loss_ratio=float(settings.STRATEGY_EXIT_STOP_LOSS_RATIO),
        exit_max_holding_min=int(settings.STRATEGY_EXIT_MAX_HOLDING_MIN),
        exit_min_take_profit_ratio=float(settings.STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO),
        exit_small_loss_tolerance_ratio=float(settings.STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO),
        slippage_bps=float(settings.STRATEGY_ENTRY_SLIPPAGE_BPS),
        live_fee_rate_estimate=float(settings.LIVE_FEE_RATE_ESTIMATE),
        entry_edge_buffer_ratio=float(settings.ENTRY_EDGE_BUFFER_RATIO),
        strategy_min_expected_edge_ratio=float(settings.STRATEGY_MIN_EXPECTED_EDGE_RATIO),
        buy_fraction=float(settings.BUY_FRACTION),
        max_order_krw=float(settings.MAX_ORDER_KRW),
        candidate_regime_policy=None,
    )

