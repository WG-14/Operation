"""The default runtime uses locally persisted market data only."""

from __future__ import annotations

from operation.cli.registry import CommandSpec


def command_specs() -> list[CommandSpec]:
    return []
