from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "bithumb_bot" / "strategy_contract"


def _imports() -> set[str]:
    imports: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
    return imports


def test_strategy_contract_does_not_import_runtime_or_broker() -> None:
    imports = _imports()

    forbidden_prefixes = (
        "bithumb_bot.runtime",
        "bithumb_bot.broker",
        "bithumb_bot.h74_live_rehearsal",
        "bithumb_bot.run_loop_execution_planner",
    )
    assert not any(name.startswith(forbidden_prefixes) for name in imports)


def test_runtime_may_import_strategy_contract() -> None:
    import bithumb_bot.strategy_contract as contract

    assert contract.StrategyDecisionV2
    assert contract.DailyParticipationReducer


def test_contract_package_has_no_settings_dependency() -> None:
    imports = _imports()

    assert "bithumb_bot.config" not in imports
    assert "settings" not in "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py"))
    assert "httpx" not in imports
    assert "sqlite3" not in imports
    assert "ensure_db" not in "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py"))
