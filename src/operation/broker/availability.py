"""Broker availability boundary for deployments without a live adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .base import Broker


LIVE_BROKER_NOT_CONFIGURED = "LIVE_BROKER_NOT_CONFIGURED"


class LiveBrokerNotConfiguredError(RuntimeError):
    """Raised before startup when live execution has no configured broker."""

    reason_code = LIVE_BROKER_NOT_CONFIGURED

    def __init__(self) -> None:
        super().__init__(
            "live execution is unavailable: no live broker is configured "
            f"(reason_code={self.reason_code})"
        )


class BrokerFactory(Protocol):
    def __call__(self) -> Broker: ...


@dataclass(frozen=True)
class UnavailableBrokerFactory:
    """Explicit fail-closed factory used until a real broker adapter is installed."""

    reason_code: str = LIVE_BROKER_NOT_CONFIGURED

    def __call__(self) -> Broker:
        raise LiveBrokerNotConfiguredError()
