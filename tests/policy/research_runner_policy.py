from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXPENSIVE_RESEARCH_MARKERS = {
    "research_e2e",
    "audit_e2e",
    "walk_forward_e2e",
    "parallel_e2e",
    "slow_research",
    "nightly",
    "memory_sensitive",
}

PRODUCTION_RESEARCH_ENTRYPOINTS = {
    "run_research_backtest",
    "run_research_walk_forward",
}

APPROVED_CONTRACT_HELPERS = {
    "_run_contract_research_backtest",
    "_run_contract_research_walk_forward",
}

INVENTORY_PATH = Path("tests/policy/research_e2e_inventory.json")


@dataclass(frozen=True)
class RunnerCall:
    path: Path
    test_name: str
    nodeid: str
    line: int
    entrypoint: str
    markers: frozenset[str]


def load_inventory(path: Path = INVENTORY_PATH) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tests = payload.get("tests")
    if not isinstance(tests, list):
        raise AssertionError(f"{path} must contain a tests list")
    inventory: dict[str, dict[str, object]] = {}
    for item in tests:
        if not isinstance(item, dict):
            raise AssertionError(f"{path} inventory entries must be objects")
        nodeid = item.get("nodeid")
        reason = item.get("reason")
        markers = item.get("markers")
        if not isinstance(nodeid, str) or not nodeid:
            raise AssertionError(f"{path} inventory entry missing nodeid")
        if not isinstance(reason, str) or not reason.strip():
            raise AssertionError(f"{path} inventory entry {nodeid} missing reason")
        if not isinstance(markers, list) or not markers:
            raise AssertionError(f"{path} inventory entry {nodeid} missing markers")
        marker_set = {marker for marker in markers if isinstance(marker, str)}
        if marker_set.isdisjoint(EXPENSIVE_RESEARCH_MARKERS):
            raise AssertionError(f"{path} inventory entry {nodeid} lacks an expensive marker")
        inventory[nodeid] = item
    return inventory


def discover_policy_violations(test_root: Path = Path("tests")) -> list[str]:
    violations: list[str] = []
    direct_calls = list(discover_direct_production_runner_calls(test_root))
    inventory = load_inventory()
    inventory_nodeids = set(inventory)
    direct_nodeids = {call.nodeid for call in direct_calls}

    stale = sorted(inventory_nodeids - direct_nodeids)
    if stale:
        violations.extend(f"stale inventory entry without direct production runner call: {nodeid}" for nodeid in stale)

    missing = sorted(direct_nodeids - inventory_nodeids)
    if missing:
        violations.extend(f"direct production runner test missing E2E inventory entry: {nodeid}" for nodeid in missing)

    for call in direct_calls:
        if call.markers.isdisjoint(EXPENSIVE_RESEARCH_MARKERS):
            violations.append(
                f"{call.path}:{call.line}:{call.test_name} calls {call.entrypoint} without an expensive marker"
            )
            continue
        entry = inventory.get(call.nodeid)
        if entry is None:
            continue
        declared_markers = set(entry.get("markers") or ())
        missing_markers = declared_markers - set(call.markers)
        if missing_markers:
            violations.append(
                f"{call.nodeid} inventory markers not present on test: {sorted(missing_markers)}"
            )

    violations.extend(validate_contract_helpers(test_root))
    return sorted(set(violations))


def discover_direct_production_runner_calls(test_root: Path) -> Iterable[RunnerCall]:
    for path in sorted(test_root.glob("test_*.py")):
        display_path = _display_path(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parent_by_id = _parent_map(tree)
        aliases = _entrypoint_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            markers = frozenset(_decorator_marker_names(node) | _class_marker_names(node, parent_by_id))
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                entrypoint = _entrypoint_call_name(call, aliases)
                if entrypoint is None:
                    continue
                yield RunnerCall(
                    path=display_path,
                    test_name=node.name,
                    nodeid=f"{display_path.as_posix()}::{node.name}",
                    line=call.lineno,
                    entrypoint=entrypoint,
                    markers=markers,
                )


def validate_contract_helpers(test_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(test_root.glob("test_*.py")):
        display_path = _display_path(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _entrypoint_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            direct_runner_calls = [
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and _entrypoint_call_name(call, aliases) is not None
            ]
            if not direct_runner_calls:
                continue
            if node.name not in APPROVED_CONTRACT_HELPERS and not node.name.startswith("test_"):
                violations.append(
                    f"{display_path}:{node.lineno}:{node.name} wraps a production research runner but is not approved"
                )
                continue
            if node.name in APPROVED_CONTRACT_HELPERS:
                for call in direct_runner_calls:
                    if not any(keyword.arg == "candidate_evaluator" for keyword in call.keywords):
                        violations.append(
                            f"{display_path}:{call.lineno}:{node.name} must inject a deterministic candidate_evaluator"
                        )
                if not _calls_name(node, "assert_fast_research_workload"):
                    violations.append(
                        f"{display_path}:{node.lineno}:{node.name} must validate workload immediately after the report"
                    )
    return violations


def _display_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return path


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _entrypoint_aliases(tree: ast.AST) -> dict[str, str]:
    aliases = {name: name for name in PRODUCTION_RESEARCH_ENTRYPOINTS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in PRODUCTION_RESEARCH_ENTRYPOINTS:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _decorator_marker_names(node: ast.FunctionDef | ast.ClassDef) -> set[str]:
    markers: set[str] = set()
    for decorator in node.decorator_list:
        current = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(current, ast.Attribute):
            markers.add(current.attr)
        elif isinstance(current, ast.Name):
            markers.add(current.id)
    return markers


def _class_marker_names(node: ast.FunctionDef, parent_by_id: dict[int, ast.AST]) -> set[str]:
    parent = parent_by_id.get(id(node))
    if isinstance(parent, ast.ClassDef):
        return _decorator_marker_names(parent)
    return set()


def _entrypoint_call_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    if isinstance(node.func, ast.Name):
        return aliases.get(node.func.id)
    if isinstance(node.func, ast.Attribute) and node.func.attr in PRODUCTION_RESEARCH_ENTRYPOINTS:
        return node.func.attr
    return None


def _calls_name(node: ast.FunctionDef, name: str) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False
