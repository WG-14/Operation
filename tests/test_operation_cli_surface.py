from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bithumb_bot.cli.parser import build_parser
from bithumb_bot.cli.registry import command_registry


REMOVED_COMMANDS = {
    "promotion-provenance-verify",
    "promotion-verify",
    "decision-equivalence",
    "candidate-regime-policy-equivalence-evidence",
    "paired-experiment",
    "research-missing-candles",
    "retry-missing-candles",
    "probe-missing-candles",
    "classify-persistent-missing-candles",
    "find-clean-candle-segments",
}

MOVED_RUNTIME_COMMANDS = {"runtime-replay-decisions", "replay-decision"}
MOVED_OPERATION_EVIDENCE_COMMANDS = {
    "h74-observation-authority-generate",
    "h74-observation-authority-verify",
    "h74-source-observation-authority-generate",
    "h74-source-observation-authority-verify",
}


def test_operation_registry_excludes_research_command_surface() -> None:
    registry = command_registry()

    assert not {name for name in registry if name.startswith("research-")}
    assert REMOVED_COMMANDS.isdisjoint(registry)


def test_operation_registry_keeps_runtime_and_h74_observation_commands() -> None:
    registry = command_registry()

    assert MOVED_RUNTIME_COMMANDS | MOVED_OPERATION_EVIDENCE_COMMANDS <= registry.keys()
    assert {registry[name].domain for name in MOVED_RUNTIME_COMMANDS} == {"runtime"}
    assert {registry[name].domain for name in MOVED_OPERATION_EVIDENCE_COMMANDS} == {"operation_evidence"}


def test_operation_help_excludes_research_commands_and_keeps_moved_commands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser(command_registry())

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])

    output = capsys.readouterr().out
    assert exc.value.code == 0
    assert "research-backtest" not in output
    assert "paired-experiment" not in output
    assert "runtime-replay-decisions" in output
    assert "h74-observation-authority-generate" in output


def test_registry_registers_only_operation_command_modules() -> None:
    registry_path = Path("src/bithumb_bot/cli/registry.py")
    tree = ast.parse(registry_path.read_text(encoding="utf-8"), filename=str(registry_path))
    source = registry_path.read_text(encoding="utf-8")
    imported_command_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "commands"
        for alias in node.names
    }

    assert {"research", "paired_experiment", "data_plane"}.isdisjoint(imported_command_modules)
    assert "operation_evidence" in imported_command_modules
    assert "yield from operation_evidence.command_specs()" in source


def test_command_registry_is_credential_free_and_does_not_execute_handlers(monkeypatch) -> None:
    for variable in ("BITHUMB_API_KEY", "BITHUMB_API_SECRET", "BITHUMB_ACCESS_KEY", "BITHUMB_SECRET_KEY"):
        monkeypatch.delenv(variable, raising=False)

    registry = command_registry()

    assert registry
    assert all(spec.handler is not None for spec in registry.values())
