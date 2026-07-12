from __future__ import annotations

from .operation_strategy.discovery import ensure_operation_strategy_plugins_discovered


_REGISTERED = False


def ensure_runtime_decision_adapters_registered() -> None:
    """Load plugin discovery so adapter resolution can derive from manifests."""
    global _REGISTERED
    if _REGISTERED:
        return
    ensure_operation_strategy_plugins_discovered()
    _REGISTERED = True


def reset_runtime_decision_adapter_bootstrap_for_tests() -> None:
    global _REGISTERED
    _REGISTERED = False
