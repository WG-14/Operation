from __future__ import annotations

from .context import AppContext
from .registry import CommandSpec


def enforce_guard_policy(spec: CommandSpec, context: AppContext) -> None:
    """Apply declarative live guard metadata before command dispatch."""

    policy = spec.guard_policy
    if not policy:
        return
    settings = context.settings
    if getattr(settings, "MODE", None) != "live":
        return

    from bithumb_bot.config import (
        LiveModeValidationError,
        validate_live_dry_run_loop_startup_contract,
        validate_live_mode_preflight,
        validate_live_run_startup_contract,
    )

    try:
        if policy == "live_run_loop":
            validate_live_run_startup_contract(settings)
        elif policy == "live_dry_run_loop":
            validate_live_dry_run_loop_startup_contract(settings)
        elif policy == "live_preflight":
            validate_live_mode_preflight(settings)
        else:
            raise RuntimeError(f"unknown CLI guard policy for {spec.name}: {policy}")
    except LiveModeValidationError as exc:
        if policy == "live_run_loop":
            from bithumb_bot.notifier import notify
            from bithumb_bot.observability import safety_event

            notify(
                safety_event(
                    "startup_gate_blocked",
                    client_order_id="-",
                    submit_attempt_id="-",
                    exchange_order_id="-",
                    reason_code="LIVE_STARTUP_GUARD",
                    alert_kind="startup_gate",
                    reason=str(exc),
                    state_to="HALTED",
                )
            )
        context.printer(f"[LIVE-COMMAND-GUARD] {exc}")
        raise SystemExit(1) from exc
