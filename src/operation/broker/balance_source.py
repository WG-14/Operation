"""Generic live-balance capability boundary.

No live adapter is configured in this distribution, so balance reads fail
closed rather than attempting a transport request.
"""

from __future__ import annotations

from dataclasses import dataclass

from .availability import LiveBrokerNotConfiguredError


@dataclass(frozen=True)
class LiveAccountEvidence:
    status: str = "unavailable"
    reason_code: str = "LIVE_BROKER_NOT_CONFIGURED"


def build_live_account_evidence(*_: object, **__: object) -> LiveAccountEvidence:
    return LiveAccountEvidence()


def fetch_balance_snapshot(*_: object, **__: object):
    raise LiveBrokerNotConfiguredError()
