from __future__ import annotations

from pathlib import Path

from bithumb_bot.research.strategy_registry import list_research_strategy_plugins


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_backtest_kernel_stays_strategy_neutral() -> None:
    source = _source("src/bithumb_bot/research/backtest_kernel.py")

    forbidden = (
        "sma_with_filter",
        "SMA_",
        "SmaPolicyConfig",
        "curr_s",
        "prev_s",
        "opposite_cross",
    )
    assert all(token not in source for token in forbidden)
    assert "ResearchDecisionEvent" in source
    assert "StrategyDecisionV2" in source
    assert "ExecutionSubmitPlan" in source


def test_backtest_engine_is_compatibility_only_for_sma_event_generation() -> None:
    source = _source("src/bithumb_bot/research/backtest_engine.py")

    forbidden = (
        "SmaWithFilterDecisionAdapter",
        "_rolling_sma_values",
        "_rolling_close_range_ratios",
        "_overextended_return_ratios",
        "class Sma",
        "curr_s",
        "prev_s",
    )
    assert all(token not in source for token in forbidden)
    assert "Compatibility wrapper" in source


def test_backtest_support_does_not_import_backtest_engine() -> None:
    source = _source("src/bithumb_bot/research/backtest_support.py")

    assert "backtest_engine" not in source


def test_strategy_registry_does_not_import_engine_owned_runners() -> None:
    source = _source("src/bithumb_bot/research/strategy_registry.py")

    forbidden = (
        "from .backtest_engine import",
        "run_sma_backtest",
        "run_noop_baseline_backtest",
        "run_buy_and_hold_baseline_backtest",
        "_rolling_sma_values",
        "_rolling_close_range_ratios",
        "_overextended_return_ratios",
    )
    assert all(token not in source for token in forbidden)


def test_research_runnable_plugins_declare_event_builders_and_capabilities() -> None:
    for plugin in list_research_strategy_plugins():
        assert plugin.runtime_capabilities is not None
        payload = plugin.contract_payload()
        assert "research_event_builder_supported" in payload
        if payload["research_runnable"]:
            assert payload["research_event_builder_supported"] is True
            assert payload["research_event_builder_module"]


def test_non_sma_canary_uses_plugin_event_builder_contract() -> None:
    plugins = {plugin.name: plugin for plugin in list_research_strategy_plugins()}
    plugin = plugins["canary_non_sma"]
    payload = plugin.contract_payload()

    assert payload["research_event_builder_supported"] is True
    assert payload["research_event_builder_module"] == "bithumb_bot.strategy_plugins.canary_non_sma"
    assert payload["runner_module"] == "bithumb_bot.strategy_plugins.canary_non_sma"
