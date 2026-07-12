from __future__ import annotations

import importlib
import os
import sys

import pytest

from operation.bootstrap import bootstrap_argv, run_cli


def _clear_explicit_env_selectors(monkeypatch) -> None:
    monkeypatch.delenv("OPERATION_ENV_FILE", raising=False)
    monkeypatch.delenv("OPERATION_ENV_FILE_LIVE", raising=False)
    monkeypatch.delenv("OPERATION_ENV_FILE_PAPER", raising=False)


def test_bootstrap_preserves_subcommand_interval_flag(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("INTERVAL", raising=False)

    argv = bootstrap_argv(
        [
            "operation-bot",
            "backfill-candles",
            "--market",
            "KRW-BTC",
            "--interval",
            "1m",
            "--start",
            "2023-01-01",
            "--end",
            "2026-05-01",
        ]
    )

    assert argv == [
        "operation-bot",
        "backfill-candles",
        "--market",
        "KRW-BTC",
        "--interval",
        "1m",
        "--start",
        "2023-01-01",
        "--end",
        "2026-05-01",
    ]
    assert "INTERVAL" not in __import__("os").environ


def test_bootstrap_consumes_legacy_global_interval_before_subcommand(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("INTERVAL", raising=False)

    argv = bootstrap_argv(["operation-bot", "--interval", "1m", "run"])

    assert argv == ["operation-bot", "run"]
    assert __import__("os").environ["INTERVAL"] == "1m"


def test_bootstrap_preserves_subcommand_interval_equals_flag(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("INTERVAL", raising=False)

    argv = bootstrap_argv(
        [
            "operation-bot",
            "backfill-candles",
            "--market",
            "KRW-BTC",
            "--interval=1m",
            "--start",
            "2023-01-01",
            "--end",
            "2026-05-01",
        ]
    )

    assert "--interval=1m" in argv
    assert argv[1] == "backfill-candles"
    assert "INTERVAL" not in __import__("os").environ


def test_bootstrap_consumes_legacy_mode_and_entry_before_subcommand(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("MODE", raising=False)
    monkeypatch.delenv("ENTRY_MODE", raising=False)

    argv = bootstrap_argv(["operation-bot", "--mode", "paper", "--entry", "breakout", "run"])

    assert argv == ["operation-bot", "run"]
    assert __import__("os").environ["MODE"] == "paper"
    assert __import__("os").environ["ENTRY_MODE"] == "breakout"


def test_bootstrap_preserves_subcommand_mode_flag(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("MODE", raising=False)

    argv = bootstrap_argv(["operation-bot", "profile-generate", "--mode", "paper"])

    assert argv == ["operation-bot", "profile-generate", "--mode", "paper"]
    assert "MODE" not in __import__("os").environ


def test_run_cli_dispatches_with_normalized_argv(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.delenv("MODE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["operation-bot", "--mode", "paper", "research-backtest", "--manifest", "m.json"],
    )
    monkeypatch.setattr("operation.observability.configure_runtime_logging", lambda: None)

    calls: list[list[str]] = []

    def fake_main(argv: list[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 0

    cli_main_module = importlib.import_module("operation.cli.main")
    monkeypatch.setattr(cli_main_module, "main", fake_main)

    with pytest.raises(SystemExit) as exc:
        run_cli()

    assert exc.value.code == 0
    assert calls == [["research-backtest", "--manifest", "m.json"]]
    assert os.environ["MODE"] == "paper"
    assert sys.argv == ["operation-bot", "research-backtest", "--manifest", "m.json"]


def test_run_cli_propagates_cli_return_code(monkeypatch) -> None:
    _clear_explicit_env_selectors(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["operation-bot", "research-readiness", "--manifest", "missing.json"])
    monkeypatch.setattr("operation.bootstrap.bootstrap_argv", lambda argv: argv)
    monkeypatch.setattr("operation.observability.configure_runtime_logging", lambda: None)
    cli_main_module = importlib.import_module("operation.cli.main")
    monkeypatch.setattr(cli_main_module, "main", lambda argv=None: 7)

    with pytest.raises(SystemExit) as exc:
        run_cli()

    assert exc.value.code == 7
