from __future__ import annotations

import ast
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bithumb_bot.cli.context import AppContext
from bithumb_bot.cli.dispatch import dispatch
from bithumb_bot.cli.parser import build_parser
from bithumb_bot.cli.registry import CommandSpec, command_registry


def test_cli_help_builds_from_registry(capsys: pytest.CaptureFixture[str]) -> None:
    sys.modules.pop("bithumb_bot.app_impl", None)
    parser = build_parser(command_registry())

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "bithumb-bot" in output
    assert "recovery-report" in output
    assert "strategy-sweep" in output
    assert "bithumb_bot.app_impl" not in sys.modules


def test_command_registration_contains_expected_major_groups() -> None:
    registry = command_registry()

    assert {
        "marketdata",
        "runtime",
        "live_ops",
        "recovery",
        "repairs",
        "reports",
        "research",
        "profile",
        "strategy",
        "data_plane",
    } <= {spec.domain for spec in registry.values()}
    assert registry["run"].guard_policy == "live_run_loop"
    assert registry["recover-order"].requires_confirmation is True
    assert registry["fee-gap-accounting-repair"].writes_db is True


def test_selected_commands_parse_with_unknown_legacy_flags() -> None:
    parser = build_parser(command_registry())

    args, unknown = parser.parse_known_args(["strategy-sweep", "--short", "5,7", "--json"])

    assert args.cmd == "strategy-sweep"
    assert unknown == ["--short", "5,7", "--json"]


def test_dispatch_uses_spec_handler_with_context() -> None:
    calls: list[tuple[str, list[str]]] = []

    def _handler(args: argparse.Namespace, context: AppContext) -> int:
        calls.append((args.cmd, list(context.argv or [])))
        return 7

    spec = CommandSpec(
        name="fake",
        domain="runtime",
        handler=_handler,
        register_parser=lambda subparsers: subparsers.add_parser("fake"),
    )

    rc = dispatch(
        argparse.Namespace(cmd="fake"),
        AppContext(argv=["fake", "--flag"]),
        {"fake": spec},
    )

    assert rc == 7
    assert calls == [("fake", ["fake", "--flag"])]


def test_live_guard_policy_is_metadata_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _handler(_args: argparse.Namespace, _context: AppContext) -> int:
        return 0

    spec = CommandSpec(
        name="guarded",
        domain="runtime",
        handler=_handler,
        register_parser=lambda subparsers: subparsers.add_parser("guarded"),
        guard_policy="live_preflight",
    )
    monkeypatch.setattr(
        "bithumb_bot.config.validate_live_mode_preflight",
        lambda _settings: calls.append("preflight"),
    )

    rc = dispatch(
        argparse.Namespace(cmd="guarded"),
        AppContext(settings=SimpleNamespace(MODE="live")),
        {"guarded": spec},
    )

    assert rc == 0
    assert calls == ["preflight"]


def test_cli_composition_modules_do_not_import_domain_internals() -> None:
    guarded = [
        Path("src/bithumb_bot/cli/main.py"),
        Path("src/bithumb_bot/cli/parser.py"),
        Path("src/bithumb_bot/cli/registry.py"),
        Path("src/bithumb_bot/cli/dispatch.py"),
        Path("src/bithumb_bot/cli/guards.py"),
    ]
    forbidden = (
        "bithumb_bot.broker",
        "bithumb_bot.db_core",
        "bithumb_bot.recovery",
        "bithumb_bot.runtime_state",
        "bithumb_bot.flatten",
        "bithumb_bot.fee_",
        "bithumb_bot.research",
        "bithumb_bot.profile_cli",
        "bithumb_bot.strategy_sweep",
    )

    for path in guarded:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = _resolve_import_from(path, node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), f"{path}: {alias.name}"
            if module is not None:
                assert not module.startswith(forbidden), f"{path}: {module}"


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str:
    module = node.module or ""
    if not node.level:
        return module
    package = path.with_suffix("").parts
    base = ".".join(package[1 : len(package) - node.level + 1])
    return f"{base}.{module}" if module else base
