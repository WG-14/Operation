from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "tests/policy/operation_research_import_allowlist.json"
SOURCE_ROOTS = (ROOT / "src/bithumb_bot", ROOT / "scripts")
RESEARCH_ROOT = ROOT / "src/bithumb_bot/research"
VALID_CATEGORIES = {
    "runtime strategy registry/spec/capability",
    "approved profile/promotion/evidence",
    "CLI command",
    "generic utility",
    "test/document/script",
}


def _research_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module and (node.module == "research" or node.module.startswith("research.")):
                imports.add("." * node.level + node.module)
            elif node.level and node.module is None and any(alias.name == "research" for alias in node.names):
                imports.add("." * node.level + "research")
            elif not node.level and node.module and (
                node.module == "bithumb_bot.research" or node.module.startswith("bithumb_bot.research.")
            ):
                imports.add(node.module)
            elif not node.level and node.module == "bithumb_bot" and any(alias.name == "research" for alias in node.names):
                imports.add("bithumb_bot.research")
    return imports


def _actual_imports() -> dict[str, list[str]]:
    imports: dict[str, list[str]] = {}
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            if path.is_relative_to(RESEARCH_ROOT):
                continue
            found = _research_imports(path)
            if found:
                imports[str(path.relative_to(ROOT))] = sorted(found)
    return dict(sorted(imports.items()))


def _allowlist() -> dict[str, dict[str, object]]:
    payload = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return payload["entries"]


def test_non_research_source_imports_match_temporary_operation_allowlist() -> None:
    allowlist = _allowlist()
    expected = {path: entry["imports"] for path, entry in allowlist.items()}
    assert _actual_imports() == expected


def test_temporary_operation_allowlist_has_migration_classification_and_reason() -> None:
    for path, entry in _allowlist().items():
        assert path.startswith(("src/bithumb_bot/", "scripts/"))
        assert entry["category"] in VALID_CATEGORIES
        assert isinstance(entry["reason"], str) and entry["reason"].strip()
        assert entry["imports"]
