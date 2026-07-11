from __future__ import annotations

import ast
from pathlib import Path

from bithumb_bot.cli.registry import command_registry


def _research_imports(source: Path) -> list[str]:
    violations: list[str] = []
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.") for alias in node.names):
                    violations.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                relative_research_import = node.level > 0 and (
                    module == "research" or (not module and any(alias.name == "research" for alias in node.names))
                )
                if relative_research_import or module.startswith("bithumb_bot.research"):
                    violations.append(str(path))
    return violations


def test_operation_has_no_research_package_imports_or_cli() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "bithumb_bot"

    assert not (source / "research").exists()
    assert _research_imports(source) == []
    assert not any(name.startswith("research-") for name in command_registry())


def test_operation_package_and_plugin_discovery_import_without_research() -> None:
    import bithumb_bot
    from bithumb_bot.operation_strategy.registry import list_operation_strategy_plugins

    plugins = list_operation_strategy_plugins()

    assert bithumb_bot.__name__ == "bithumb_bot"
    assert {plugin.name for plugin in plugins} >= {"safe_hold", "sma_with_filter"}
