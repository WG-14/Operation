from __future__ import annotations

from .runtime_adapters.safe_hold import SAFE_HOLD_STRATEGY_NAME, SafeHoldRuntimeDecisionAdapter
from .runtime_adapters.sma_with_filter import SmaWithFilterRuntimeDecisionAdapter
from .runtime_strategy_decision import register_runtime_decision_adapter


_REGISTERED = False


def ensure_runtime_decision_adapters_registered() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    register_runtime_decision_adapter("sma_with_filter", SmaWithFilterRuntimeDecisionAdapter)
    register_runtime_decision_adapter(SAFE_HOLD_STRATEGY_NAME, SafeHoldRuntimeDecisionAdapter)
    _REGISTERED = True
