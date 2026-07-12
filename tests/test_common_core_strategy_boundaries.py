from __future__ import annotations

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

COMMON_CORE_FILES = (
    "src/operation/strategy_decision_service.py",
    "src/operation/runtime_data_provider.py",
    "src/operation/runtime_strategy_decision.py",
    "src/operation/runtime_strategy_set.py",
    "src/operation/execution_service.py",
    "src/operation/run_loop_execution_planner.py",
)

BUILT_IN_STRATEGY_LITERALS = (
    "sma_with_filter",
    "canary_non_sma",
    "replay_threshold",
    "threshold_research_only",
    "safe_hold",
    "noop_baseline",
    "buy_and_hold_baseline",
)


def test_common_core_files_do_not_contain_concrete_strategy_literals() -> None:
    failures: list[str] = []
    for relative in COMMON_CORE_FILES:
        path = REPO / relative
        source = path.read_text(encoding="utf-8-sig")
        failures.extend(_direct_literal_failures(relative, source))
        failures.extend(_ast_branch_literal_failures(relative, source))

    assert failures == []


def _direct_literal_failures(relative: str, source: str) -> list[str]:
    failures: list[str] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        for literal in BUILT_IN_STRATEGY_LITERALS:
            if literal in line:
                failures.append(
                    f"{relative}:{line_no}: direct_string_scan concrete strategy literal {literal!r}"
                )
    return failures


def _ast_branch_literal_failures(relative: str, source: str) -> list[str]:
    tree = ast.parse(source, filename=relative)
    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for literal in _strategy_literals_in_node(node):
                failures.append(
                    f"{relative}:{node.lineno}: ast_compare concrete strategy literal {literal!r}"
                )
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for literal in _strategy_literals_in_node(case.pattern):
                    line_no = getattr(case.pattern, "lineno", getattr(node, "lineno", 0))
                    failures.append(
                        f"{relative}:{line_no}: ast_match concrete strategy literal {literal!r}"
                    )
        elif isinstance(node, ast.If):
            for literal in _strategy_literals_in_node(node.test):
                failures.append(
                    f"{relative}:{node.lineno}: ast_if_branch concrete strategy literal {literal!r}"
                )
    return failures


def _strategy_literals_in_node(node: ast.AST) -> set[str]:
    observed: set[str] = set()
    for child in ast.walk(node):
        value: object | None = None
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            value = child.value
        elif isinstance(child, ast.MatchValue) and isinstance(child.value, ast.Constant):
            value = child.value.value
        if isinstance(value, str) and value in BUILT_IN_STRATEGY_LITERALS:
            observed.add(value)
    return observed
