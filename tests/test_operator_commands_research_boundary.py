from __future__ import annotations

import ast
import builtins
import importlib
import sys
from pathlib import Path


OPERATOR_COMMANDS_PATH = Path("src/bithumb_bot/operator_commands.py")


def _direct_research_imports() -> set[str]:
    tree = ast.parse(OPERATOR_COMMANDS_PATH.read_text(encoding="utf-8"), filename=str(OPERATOR_COMMANDS_PATH))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("bithumb_bot.research"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "research" or node.module.startswith("research."):
                imports.add("." * node.level + node.module)
            elif node.module == "bithumb_bot.research" or node.module.startswith("bithumb_bot.research."):
                imports.add(node.module)
    return imports


def test_operator_commands_has_no_direct_research_imports() -> None:
    assert _direct_research_imports() == set()


def test_operator_commands_import_does_not_load_removed_research_facades_or_side_effects(monkeypatch) -> None:
    removed_facades = {
        "bithumb_bot.research.cli",
        "bithumb_bot.research.readiness",
        "bithumb_bot.research.data_plane",
        "bithumb_bot.research.execution_calibration",
    }
    for module_name in removed_facades:
        sys.modules.pop(module_name, None)
    sys.modules.pop("bithumb_bot.operator_commands", None)

    submitted: list[object] = []
    notified: list[object] = []
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        caller = str((globals or {}).get("__name__") or "")
        is_research_import = (
            name == "bithumb_bot.research"
            or name.startswith("bithumb_bot.research.")
            or name == "research"
            or name.startswith("research.")
        )
        if caller == "bithumb_bot.operator_commands" and is_research_import:
            raise AssertionError(f"operator_commands attempted blocked research import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr("bithumb_bot.broker.bithumb.BithumbBroker.place_order", lambda *args, **kwargs: submitted.append((args, kwargs)))
    monkeypatch.setattr("bithumb_bot.notifier.notify", lambda *args, **kwargs: notified.append((args, kwargs)))

    module = importlib.import_module("bithumb_bot.operator_commands")

    assert module is not None
    assert submitted == []
    assert notified == []
