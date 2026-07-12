from __future__ import annotations

from dataclasses import replace

from operation.cli.main import main
from operation.cli.parser import build_parser
from operation.cli.registry import command_registry
from operation.config import settings


def test_registry_names_are_unique_and_all_parsers_build() -> None:
    registry = command_registry()
    assert len(registry) == len(set(registry))
    assert build_parser(registry).prog == "operation"
    assert all(callable(spec.handler) for spec in registry.values())


def test_broker_dependent_commands_are_not_registered() -> None:
    names = set(command_registry())
    assert {"recover-order", "backfill-broker-order", "live-dry-run"}.isdisjoint(names)


def test_no_command_prints_usage_without_ticker_fallback(capsys) -> None:
    assert main([]) == 0
    assert "usage: operation" in capsys.readouterr().out


def test_live_run_fails_closed_before_handler(monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    context = SimpleNamespace(settings=replace(settings, MODE="live"), printer=print, env_summary=None)
    monkeypatch.setattr(
        "operation.config.validate_live_run_startup_contract",
        lambda _settings: (_ for _ in ()).throw(__import__("operation.config", fromlist=["LiveModeValidationError"]).LiveModeValidationError("LIVE_BROKER_NOT_CONFIGURED")),
    )
    monkeypatch.setattr("operation.config.log_live_execution_contract", lambda *_args, **_kwargs: None)
    try:
        main(["run"], context=context)
    except SystemExit as exc:
        assert exc.code == 1
    else:  # pragma: no cover
        raise AssertionError("live run unexpectedly proceeded")
    assert "LIVE_BROKER_NOT_CONFIGURED" in capsys.readouterr().out
