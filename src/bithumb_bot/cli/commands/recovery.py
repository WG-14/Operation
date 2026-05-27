from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    specs = legacy_specs(
        [
            "recovery-report",
            "repair-plan",
            "restart-checklist",
            "residual-closeout-plan",
            "diagnose-fill-trade-linkage",
        ],
        domain="recovery",
        read_only=True,
        json_output_supported=True,
    )
    specs.extend(
        legacy_specs(
            ["recover-order"],
            domain="recovery",
            read_only=False,
            mutating=True,
            requires_live=True,
            guard_policy="live_preflight",
            requires_confirmation=True,
            writes_db=True,
            uses_broker=True,
        )
    )
    specs.extend(
        legacy_specs(
            ["backfill-broker-order"],
            domain="recovery",
            read_only=False,
            mutating=True,
            requires_live=True,
            requires_confirmation=True,
            writes_db=True,
            uses_broker=True,
        )
    )
    return specs
