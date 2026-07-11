"""Deterministic Operation plugin discovery, independent of research."""
from __future__ import annotations

from collections.abc import Iterable
from importlib import metadata
from typing import Any

from .plugin import OperationStrategyPlugin
from .registry import register_operation_strategy_plugin

ENTRY_POINT_GROUP = "bithumb_bot.operation_strategy_plugins"
_LOADED = False


def _coerce(value: Any) -> Iterable[OperationStrategyPlugin]:
    candidate = value() if callable(value) and not isinstance(value, OperationStrategyPlugin) else value
    if isinstance(candidate, OperationStrategyPlugin):
        yield candidate
        return
    for plugin in candidate:
        if not isinstance(plugin, OperationStrategyPlugin):
            raise TypeError(f"operation_strategy_plugin_invalid_type:{type(plugin).__name__}")
        yield plugin


def iter_builtin_operation_strategy_plugins() -> Iterable[OperationStrategyPlugin]:
    from .builtin import BUILTIN_OPERATION_STRATEGY_PLUGINS
    yield from BUILTIN_OPERATION_STRATEGY_PLUGINS


def iter_entry_point_operation_strategy_plugins() -> Iterable[OperationStrategyPlugin]:
    points = metadata.entry_points()
    selected = points.select(group=ENTRY_POINT_GROUP) if hasattr(points, "select") else points.get(ENTRY_POINT_GROUP, ())
    for point in sorted(selected, key=lambda item: (str(item.name), str(item.value))):
        yield from _coerce(point.load())


def ensure_operation_strategy_plugins_discovered() -> None:
    global _LOADED
    if _LOADED:
        return
    for plugin in (*iter_builtin_operation_strategy_plugins(), *iter_entry_point_operation_strategy_plugins()):
        register_operation_strategy_plugin(plugin, replace=False)
    _LOADED = True


def reset_operation_strategy_discovery_for_tests() -> None:
    global _LOADED
    _LOADED = False
