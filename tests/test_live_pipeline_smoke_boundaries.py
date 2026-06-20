from __future__ import annotations

from bithumb_bot.research.strategy_registry import resolve_research_strategy_plugin, strategy_runtime_capability_issues
from bithumb_bot.strategy_plugins.daily_participation_sma import DAILY_PARTICIPATION_SMA_SPEC
from bithumb_bot.runtime.live_pipeline_smoke_decision import SMOKE_DECISION_CONTEXT


def test_h74_and_daily_participation_are_not_smoke_authority() -> None:
    assert DAILY_PARTICIPATION_SMA_SPEC.strategy_name == "daily_participation_sma"
    assert SMOKE_DECISION_CONTEXT["strategy_name"] == "operator_live_pipeline_smoke"
    assert SMOKE_DECISION_CONTEXT["h74_bypass"] is False
    assert SMOKE_DECISION_CONTEXT["normal_strategy_gate_modified"] is False
    assert SMOKE_DECISION_CONTEXT["strategy_performance_gate_enforced"] is False


def test_canary_non_sma_remains_not_live_real_order_allowed() -> None:
    plugin = resolve_research_strategy_plugin("canary_non_sma")
    assert plugin.runtime_capabilities.live_real_order_allowed is False


def test_ordinary_live_capability_validation_remains_fail_closed() -> None:
    issues = strategy_runtime_capability_issues(
        "canary_non_sma",
        live_dry_run=False,
        live_real_order_armed=True,
        approved_profile_path="/abs/profile.json",
    )
    assert any(issue.startswith("live_real_order_not_allowed_for_strategy:canary_non_sma") for issue in issues)
