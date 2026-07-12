from __future__ import annotations

from pathlib import Path

import pytest

import operation
from operation.broker.availability import LiveBrokerNotConfiguredError, UnavailableBrokerFactory
from operation.config import LiveModeValidationError, validate_live_mode_preflight
from operation.execution_service import live_execute_signal
from operation.runtime.app_container import create_default_runtime_app
from operation.runtime.startup_controller import StartupController


def test_live_startup_is_blocked_before_reconcile_when_broker_is_unavailable() -> None:
    reconciled = False

    def reconcile(_broker: object) -> None:
        nonlocal reconciled
        reconciled = True

    controller = StartupController(
        symbol="KRW-BTC",
        startup_gate_evaluator=lambda: None,
        state_snapshot=lambda: object(),
        latest_order_identifiers=lambda: (None, None),
        count_open_orders=lambda: 0,
        position_summary=lambda: "flat",
        recommended_commands=lambda **_: [],
        auto_recovery_allowed=lambda **_: False,
        broker_factory=UnavailableBrokerFactory(),
        initial_reconcile=reconcile,
    )

    result = controller.prepare_runtime_start(live_mode=True)

    assert result.status == "BLOCKED"
    assert result.reason_code == "LIVE_BROKER_NOT_CONFIGURED"
    assert reconciled is False


def test_package_identity_and_retired_exchange_residue_are_absent() -> None:
    assert operation.__name__ == "operation"
    root = Path(__file__).resolve().parents[1]
    forbidden = "bit" + "humb"
    tracked = (root / ".git").exists()
    assert tracked
    import subprocess

    paths = subprocess.run(["git", "ls-files"], cwd=root, check=True, text=True, capture_output=True).stdout.splitlines()
    assert not any(
        forbidden in candidate.read_text(encoding="utf-8", errors="ignore").lower()
        for path in paths
        if (candidate := root / path).is_file()
    )
    for retired_path in (
        "src/operation/runtime/live_pipeline_smoke_decision.py",
        "src/operation/broker/live.py",
    ):
        assert not (root / retired_path).exists()


def test_default_paper_market_sync_is_offline_noop() -> None:
    app = create_default_runtime_app()

    assert type(app.broker_factory).__name__ == "UnavailableBrokerFactory"
    assert app.market_sync(quiet=True, limit=1) is None


def test_live_preflight_fails_closed_without_running_broker_validation() -> None:
    class LiveSettings:
        MODE = "live"

    try:
        validate_live_mode_preflight(LiveSettings())
    except LiveModeValidationError as exc:
        assert "LIVE_BROKER_NOT_CONFIGURED" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("live preflight unexpectedly passed")


def test_live_execution_entrypoint_fails_closed_without_adapter() -> None:
    with pytest.raises(LiveBrokerNotConfiguredError):
        live_execute_signal(object(), "BUY", 1, 1.0)
