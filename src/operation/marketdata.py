"""Offline market-data compatibility surface.

External synchronization has been removed. Runtime candle reads use SQLite
through ``runtime_data_access``; operator data ingestion must be explicit.
"""

from __future__ import annotations

from .markets import canonical_market_id


class OfflineMarketDataUnavailable(RuntimeError):
    reason_code = "OFFLINE_MARKET_DATA_ONLY"


def cmd_sync(*, quiet: bool = False, limit: int = 200) -> None:
    del quiet, limit


def _unsupported(*_: object, **__: object):
    raise OfflineMarketDataUnavailable(
        "external market-data access is not configured (reason_code=OFFLINE_MARKET_DATA_ONLY)"
    )


fetch_orderbook_top = _unsupported
fetch_orderbook_tops = _unsupported
validated_best_quote_prices = _unsupported
validated_best_quote_ask_price = _unsupported
cmd_sync_orderbook_top = _unsupported
cmd_ticker = _unsupported
cmd_candles = _unsupported

__all__ = ["OfflineMarketDataUnavailable", "canonical_market_id", "cmd_sync"]
