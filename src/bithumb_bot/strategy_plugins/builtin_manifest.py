from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class BuiltinStrategyPluginExport:
    module: str
    object_name: str

    @property
    def object_path(self) -> str:
        return f"{self.module}:{self.object_name}"

    def load(self) -> Any:
        module = import_module(self.module)
        return getattr(module, self.object_name)


BUILTIN_STRATEGY_PLUGIN_EXPORTS: tuple[BuiltinStrategyPluginExport, ...] = (
    BuiltinStrategyPluginExport(
        "bithumb_bot.operation_strategy.builtin",
        "BUILTIN_OPERATION_STRATEGY_PLUGINS",
    ),
)


def iter_builtin_strategy_plugin_exports() -> tuple[BuiltinStrategyPluginExport, ...]:
    return BUILTIN_STRATEGY_PLUGIN_EXPORTS


def iter_builtin_strategy_plugins_from_manifest() -> Iterable[Any]:
    for plugin_export in BUILTIN_STRATEGY_PLUGIN_EXPORTS:
        yield plugin_export.load()
