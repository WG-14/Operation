from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bithumb_bot.operation_approval import (
    build_operation_approval,
    diff_operation_approval_to_runtime,
)
from bithumb_bot.operation_strategy.spec import materialized_strategy_parameters_hash


def _runtime() -> dict[str, object]:
    return {
        "mode": "live", "live_dry_run": True, "live_real_order_armed": False,
        "strategy_name": "sma_with_filter", "strategy_version": "v1",
        "strategy_spec_hash": "sha256:spec", "strategy_plugin_contract_hash": "sha256:plugin",
        "market": "KRW-BTC", "interval": "1m", "strategy_parameters": {"SMA_SHORT": 7},
        "strategy_parameters_hash": materialized_strategy_parameters_hash({"SMA_SHORT": 7}),
        "exit_policy_hash": "sha256:exit", "risk_policy": {
            "schema_version": 1, "max_daily_loss_krw": 1.0, "max_position_loss_pct": 0.0,
            "max_daily_order_count": 1, "max_trade_count_per_day": 1, "max_drawdown_pct": 0.0,
            "cooldown_after_loss_min": 0, "kill_switch": False, "max_open_positions": 1,
            "unresolved_order_policy": "block", "policy_status": "enabled",
            "missing_policy": "fail_closed_for_live", "source": "operation_runtime_settings",
        },
        "risk_policy_hash": "", "execution_contract_hash": "sha256:execution", "max_order_krw": 50_000.0,
    }


def test_operation_approval_fails_closed_for_parameter_and_mode_drift() -> None:
    runtime = _runtime()
    from bithumb_bot.risk_contract import RiskPolicy

    runtime["risk_policy_hash"] = RiskPolicy(**runtime["risk_policy"]).policy_hash()  # type: ignore[arg-type]
    approval = build_operation_approval(
        runtime=runtime,
        approved_by="operator",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        allowed_modes=["live_dry_run"],
    )

    assert not diff_operation_approval_to_runtime(approval, runtime)
    drifted = {**runtime, "strategy_parameters": {"SMA_SHORT": 8}}
    fields = {item["field"] for item in diff_operation_approval_to_runtime(approval, drifted)}
    assert "strategy_parameters.SMA_SHORT" in fields
    blocked_mode = {**runtime, "live_dry_run": False, "live_real_order_armed": True}
    fields = {item["field"] for item in diff_operation_approval_to_runtime(approval, blocked_mode)}
    assert "allowed_mode" in fields
