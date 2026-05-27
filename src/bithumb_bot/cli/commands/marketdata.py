from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "ticker",
            "candles",
            "sync",
            "sync-orderbook-top",
            "backfill-candles",
        ],
        domain="marketdata",
        writes_db=True,
        produces_artifact=False,
    )
