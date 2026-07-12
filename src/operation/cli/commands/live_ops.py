"""Local operational controls that do not require a live broker."""

from __future__ import annotations

import argparse

from operation.cli.registry import CommandSpec

from ._helpers import make_spec


def _pause(_args: argparse.Namespace, _context) -> None:
    from operation.operator_commands import cmd_pause

    cmd_pause()


def command_specs() -> list[CommandSpec]:
    return [
        make_spec(
            "pause",
            domain="live_ops",
            handler=_pause,
            help="persistently pause new trading",
            read_only=False,
            mutating=True,
            writes_db=True,
        )
    ]
