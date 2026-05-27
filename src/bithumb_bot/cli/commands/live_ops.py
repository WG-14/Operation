from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    specs = legacy_specs(
        [
            "pause",
            "resume",
            "reconcile",
            "broker-diagnose",
            "target-delta-dry-run",
        ],
        domain="live_ops",
        read_only=False,
        mutating=True,
        requires_live=True,
        uses_broker=True,
        writes_db=True,
    )
    specs.extend(
        legacy_specs(
            ["panic-stop", "flatten-position", "cancel-open-orders", "target-closeout"],
            domain="live_ops",
            read_only=False,
            mutating=True,
            requires_live=True,
            guard_policy="live_preflight",
            uses_broker=True,
            writes_db=True,
        )
    )
    return specs
