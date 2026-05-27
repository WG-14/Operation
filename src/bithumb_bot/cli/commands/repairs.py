from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "fee-gap-accounting-repair",
            "fee-pending-accounting-repair",
            "rebuild-position-authority",
            "record-external-cash-adjustment",
            "manual-flat-accounting-repair",
            "external-position-accounting-repair",
        ],
        domain="repairs",
        read_only=False,
        mutating=True,
        requires_confirmation=True,
        writes_db=True,
        produces_artifact=True,
        json_output_supported=True,
    )
