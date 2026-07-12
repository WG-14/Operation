from __future__ import annotations

import argparse
from collections.abc import Mapping

from .registry import CommandSpec


def build_parser(registry: Mapping[str, CommandSpec] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operation")
    subparsers = parser.add_subparsers(dest="cmd", required=False)
    specs = registry.values() if registry is not None else ()
    for spec in sorted(specs, key=lambda item: item.name):
        spec.register_parser(subparsers)
    return parser
