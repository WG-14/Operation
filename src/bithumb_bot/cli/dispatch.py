from __future__ import annotations

import argparse
from collections.abc import Mapping

from .context import AppContext
from .guards import enforce_guard_policy
from .registry import CommandSpec


def dispatch(
    args: argparse.Namespace,
    context: AppContext,
    registry: Mapping[str, CommandSpec],
) -> int:
    command_name = getattr(args, "cmd", None) or "ticker"
    spec = registry.get(command_name)
    if spec is None:
        return 2
    enforce_guard_policy(spec, context)
    result = spec.handler(args, context)
    return 0 if result is None else int(result)
