"""Live order submission boundary.

Transport-specific submission is intentionally absent until a broker adapter
is installed and wired through ``BrokerFactory``.
"""

from __future__ import annotations

from .availability import LiveBrokerNotConfiguredError


def plan_place_order(*_: object, **__: object):
    raise LiveBrokerNotConfiguredError()


def build_place_order_payload(*_: object, **__: object):
    raise LiveBrokerNotConfiguredError()
