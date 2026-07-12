"""Offline-first market-data boundary used by the runtime."""

from __future__ import annotations

from typing import Protocol


class MarketDataProvider(Protocol):
    def sync_candles(self, *, pair: str, interval: str, limit: int) -> None: ...


class NoopMarketDataProvider:
    """Default runtime provider.

    Candle reads remain SQLite-backed; this provider intentionally performs no
    implicit network synchronization.
    """

    def sync_candles(self, *, pair: str, interval: str, limit: int) -> None:
        del pair, interval, limit

