"""Local market identifier validation.

The runtime deliberately does not discover markets from a remote catalog.
Configured symbols are canonicalized locally and data availability is checked
against the SQLite store by the runtime data preflight.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class MarketCatalogError(ValueError):
    pass


class UnsupportedMarketError(MarketCatalogError):
    pass


class ExchangeMarketCodeError(MarketCatalogError):
    pass


_MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


@dataclass(frozen=True)
class MarketInfo:
    market: str
    korean_name: str = ""
    english_name: str = ""
    market_warning: str = "NONE"


class MarketRegistry:
    def __init__(self, markets: list[MarketInfo] | None = None) -> None:
        self._markets = {item.market: item for item in markets or []}

    def get(self, market: str) -> MarketInfo | None:
        return self._markets.get(parse_documented_market_code(market))

    def items(self) -> list[MarketInfo]:
        return list(self._markets.values())


def parse_documented_market_code(market: str) -> str:
    value = str(market or "").strip().upper()
    if not _MARKET_RE.fullmatch(value):
        raise ExchangeMarketCodeError(f"invalid canonical market code: {market!r}")
    return value


def parse_user_market_input(market: str, *, default_quote: str = "KRW") -> str:
    value = str(market or "").strip().upper().replace("_", "-")
    if "-" not in value and value:
        value = f"{default_quote.upper()}-{value}"
    return parse_documented_market_code(value)


def normalize_market_id(market: str) -> str:
    return parse_user_market_input(market)


def canonical_market_id(market: str, *, registry: MarketRegistry | None = None) -> str:
    del registry
    return parse_user_market_input(market)


def canonical_market_with_raw(market: str) -> tuple[str, str]:
    raw = str(market or "")
    return canonical_market_id(raw), raw


def validate_exchange_market_id(market: str, *, registry: MarketRegistry | None = None) -> str:
    del registry
    return canonical_market_id(market)


def validate_exchange_market_code(market: str, *, registry: MarketRegistry | None = None) -> str:
    return validate_exchange_market_id(market, registry=registry)


def get_market_registry(**_: object) -> MarketRegistry:
    return MarketRegistry()


def evaluate_market_warning_policy(*, raw_warning: object, warning_block_states: set[str]):
    from dataclasses import make_dataclass

    decision_type = make_dataclass("MarketWarningPolicyDecision", [("normalized_warning", str), ("is_warning_state", bool), ("should_block", bool)], frozen=True)
    warning = str(raw_warning or "NONE").strip().upper() or "NONE"
    return decision_type(warning, warning != "NONE", warning in warning_block_states)
