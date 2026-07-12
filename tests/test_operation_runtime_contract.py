from __future__ import annotations

import json
from dataclasses import replace

import pytest

from operation.config import LiveModeValidationError, settings, validate_runtime_strategy_set_selection
from operation.operation_strategy.registry import (
    list_operation_strategy_plugins,
    operation_strategy_runtime_capability_issues,
)


def test_operation_plugin_discovery_and_safe_hold_fail_closed() -> None:
    plugins = {plugin.name: plugin for plugin in list_operation_strategy_plugins()}
    issues = operation_strategy_runtime_capability_issues(
        "safe_hold",
        live_dry_run=True,
        live_real_order_armed=False,
        require_runtime_replay=True,
    )

    assert "safe_hold" in plugins
    assert "live_dry_run_not_allowed_for_strategy:safe_hold:safe_hold_runtime_fallback_not_live_eligible" in issues


def test_operation_runtime_strategy_set_rejects_multi_pair_configuration() -> None:
    cfg = replace(
        settings,
        PAIR="KRW-BTC",
        INTERVAL="1m",
        RUNTIME_STRATEGY_SET_JSON=json.dumps(
            {
                "market_scope": {"mode": "single_pair", "pair": "KRW-BTC", "interval": "1m"},
                "strategies": [
                    {"strategy_name": "safe_hold", "pair": "KRW-BTC", "interval": "1m"},
                    {"strategy_name": "safe_hold", "pair": "KRW-ETH", "interval": "1m"},
                ],
            }
        ),
    )

    with pytest.raises(LiveModeValidationError, match="multi_pair_runtime_unsupported"):
        validate_runtime_strategy_set_selection(cfg)
