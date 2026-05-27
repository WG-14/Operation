from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        ["strategy-sweep"],
        domain="strategy",
        read_only=True,
        produces_artifact=False,
        json_output_supported=True,
    )
