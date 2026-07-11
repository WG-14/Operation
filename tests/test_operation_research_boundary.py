from __future__ import annotations

import ast
from pathlib import Path

from bithumb_bot.cli.registry import command_registry


FORBIDDEN_MODULES = (
    "bithumb_bot.profile_cli",
    "bithumb_bot.approved_profile",
    "bithumb_bot.paired_experiment",
    "bithumb_bot.paired_experiment_diff",
    "bithumb_bot.research",
)


def _forbidden_imports(source: Path) -> list[str]:
    violations: list[str] = []
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == module or alias.name.startswith(module + ".") for alias in node.names for module in FORBIDDEN_MODULES):
                    violations.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                relative_research_import = node.level > 0 and (
                    module == "research" or (not module and any(alias.name == "research" for alias in node.names))
                )
                if relative_research_import or any(module == forbidden or module.startswith(forbidden + ".") for forbidden in FORBIDDEN_MODULES):
                    violations.append(str(path))
    return violations


def test_operation_and_tests_do_not_import_deleted_research_modules_or_cli() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "bithumb_bot"

    assert not (source / "research").exists()
    tests = root / "tests"
    assert _forbidden_imports(source) == []
    assert _forbidden_imports(tests) == []
    assert not any(name.startswith("research-") for name in command_registry())


def test_operation_package_and_plugin_discovery_import_without_research() -> None:
    import bithumb_bot
    from bithumb_bot.operation_strategy.registry import list_operation_strategy_plugins

    plugins = list_operation_strategy_plugins()

    assert bithumb_bot.__name__ == "bithumb_bot"
    assert {plugin.name for plugin in plugins} >= {"safe_hold", "sma_with_filter"}
