from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "research-missing-candles",
            "retry-missing-candles",
            "classify-persistent-missing-candles",
        ],
        domain="data_plane",
        read_only=False,
        mutating=True,
        produces_artifact=True,
        json_output_supported=False,
    )
