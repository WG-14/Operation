"""Exchange-neutral top-of-book value objects.

These objects carry already-observed quotes for paper execution and durable
local snapshots.  They intentionally perform no network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BestQuote:
    market: str
    bid_price: float
    ask_price: float
    source: str = "local_observation"
    observed_at_epoch_sec: float | None = None


@dataclass(frozen=True)
class OrderbookUnit:
    bid_price: float
    ask_price: float
    bid_size: float | None = None
    ask_size: float | None = None

    @property
    def has_depth_size(self) -> bool:
        return self.bid_size is not None and self.ask_size is not None


@dataclass(frozen=True)
class OrderbookSnapshot:
    market: str
    orderbook_units: tuple[OrderbookUnit, ...]
    timestamp: int = 0
    source: str = "local_observation"
