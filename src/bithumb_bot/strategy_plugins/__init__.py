from __future__ import annotations

"""Compatibility namespace for runtime strategy implementation helpers.

Plugin discovery is Operation-owned in ``bithumb_bot.operation_strategy``.
"""

from collections.abc import Iterable
from typing import Any


def iter_discovered_strategy_plugins() -> Iterable[Any]:
    """Research registry compatibility hook: Operation no longer supplies plugins."""
    return iter(())
