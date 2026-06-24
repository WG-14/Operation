from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .context import AppContext


ParserRegistrar = Callable[[argparse._SubParsersAction], None]
CommandHandler = Callable[[argparse.Namespace, AppContext], int | None]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    domain: str
    handler: CommandHandler
    register_parser: ParserRegistrar
    read_only: bool = True
    mutating: bool = False
    requires_live: bool = False
    guard_policy: str | None = None
    requires_confirmation: bool = False
    writes_db: bool = False
    uses_broker: bool = False
    produces_artifact: bool = False
    json_output_supported: bool = False


def iter_command_specs() -> Iterable[CommandSpec]:
    from .commands import (
        data_plane,
        live_ops,
        marketdata,
        paired_experiment,
        profile,
        recovery,
        repairs,
        reports,
        research,
        runtime,
        strategy,
    )

    yield from marketdata.command_specs()
    yield from runtime.command_specs()
    yield from live_ops.command_specs()
    yield from recovery.command_specs()
    yield from repairs.command_specs()
    yield from reports.command_specs()
    yield from research.command_specs()
    yield from paired_experiment.command_specs()
    yield from profile.command_specs()
    yield from strategy.command_specs()
    yield from data_plane.command_specs()


def command_registry() -> Mapping[str, CommandSpec]:
    specs = list(iter_command_specs())
    by_name = {spec.name: spec for spec in specs}
    if len(by_name) != len(specs):
        names = [spec.name for spec in specs]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise RuntimeError(f"duplicate CLI command specs: {', '.join(duplicates)}")
    return by_name
