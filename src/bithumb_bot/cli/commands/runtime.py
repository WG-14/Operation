from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    specs = legacy_specs(
        [
            "signal",
            "explain",
            "status",
            "health",
            "audit",
            "check",
            "audit-ledger",
            "validate-db",
            "config-dump",
            "orders",
            "fills",
            "trades",
        ],
        domain="runtime",
        read_only=True,
        json_output_supported=True,
    )
    specs.extend(
        legacy_specs(
            ["run"],
            domain="runtime",
            read_only=False,
            mutating=True,
            guard_policy="live_run_loop",
            writes_db=True,
            uses_broker=True,
        )
    )
    specs.extend(
        legacy_specs(
            ["live-dry-run"],
            domain="runtime",
            read_only=False,
            mutating=True,
            guard_policy="live_dry_run_loop",
            writes_db=True,
            uses_broker=True,
        )
    )
    return specs
