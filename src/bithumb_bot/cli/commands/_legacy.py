from __future__ import annotations

import argparse
from collections.abc import Iterable

from bithumb_bot.cli.context import AppContext
from bithumb_bot.cli.registry import CommandSpec


def register_name(
    subparsers: argparse._SubParsersAction,
    name: str,
) -> None:
    subparsers.add_parser(name)


def dispatch_to_legacy_app(_args: argparse.Namespace, context: AppContext) -> int:
    from bithumb_bot import app_impl

    argv = list(context.argv or [])
    legacy_main = getattr(app_impl, "legacy_main", app_impl.main)
    return int(legacy_main(argv))


def legacy_specs(
    names: Iterable[str],
    *,
    domain: str,
    read_only: bool = True,
    mutating: bool = False,
    requires_live: bool = False,
    guard_policy: str | None = None,
    requires_confirmation: bool = False,
    writes_db: bool = False,
    uses_broker: bool = False,
    produces_artifact: bool = False,
    json_output_supported: bool = False,
) -> list[CommandSpec]:
    return [
        CommandSpec(
            name=name,
            domain=domain,
            handler=dispatch_to_legacy_app,
            register_parser=lambda subparsers, command_name=name: register_name(subparsers, command_name),
            read_only=read_only,
            mutating=mutating,
            requires_live=requires_live,
            guard_policy=guard_policy,
            requires_confirmation=requires_confirmation,
            writes_db=writes_db,
            uses_broker=uses_broker,
            produces_artifact=produces_artifact,
            json_output_supported=json_output_supported,
        )
        for name in names
    ]
