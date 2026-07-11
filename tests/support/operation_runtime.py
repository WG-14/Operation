from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from bithumb_bot.config import settings
from bithumb_bot.decision_equivalence import sha256_prefixed
from bithumb_bot import runtime_state
from bithumb_bot.db_core import ensure_db
from bithumb_bot.runtime.app_container import create_default_runtime_app
from bithumb_bot.runtime.operator_event_composer import RuntimeOperatorEventComposer
from bithumb_bot.runtime.runner import Runner
from bithumb_bot.runtime.runtime_checkpoint import RuntimeCheckpoint
from bithumb_bot.runtime.safety_controller import HaltReason
from bithumb_bot.runtime_compat import evaluate_startup_safety_gate


def set_live_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_dir: Path,
    db_path: Path | None = None,
) -> None:
    """Configure repository-external, mode-separated paths for live safety tests."""
    roots = {
        "ENV_ROOT": (base_dir / "env").resolve(),
        "RUN_ROOT": (base_dir / "run").resolve(),
        "DATA_ROOT": (base_dir / "data").resolve(),
        "LOG_ROOT": (base_dir / "logs").resolve(),
        "BACKUP_ROOT": (base_dir / "backup").resolve(),
    }
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("RUN_LOCK_PATH", str((roots["RUN_ROOT"] / "live" / "bithumb-bot.lock").resolve()))
    live_db_path = (
        db_path.resolve()
        if db_path is not None
        else (roots["DATA_ROOT"] / "live" / "trades" / "live.sqlite").resolve()
    )
    monkeypatch.setenv("DB_PATH", str(live_db_path))
    object.__setattr__(settings, "DB_PATH", str(live_db_path))


def unit_runtime_strategy_set_manifest(**_kwargs: object) -> dict[str, object]:
    """Minimal Operation-owned manifest for restart and recovery test fixtures."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "authority_label": "RuntimeStrategySetManifest",
        "authority_scope": "operator_reproducibility_manifest",
        "source": "unit",
        "runtime_pair": "KRW-BTC",
        "runtime_interval": "1m",
        "single_pair_runtime_enforced": True,
        "market_scope": {
            "schema_version": 1,
            "mode": "single_pair",
            "pair": "KRW-BTC",
            "interval": "1m",
        },
        "multi_strategy_enabled": False,
        "active_strategy_count": 1,
        "active_strategy_pairs": ["KRW-BTC"],
        "active_strategy_intervals": ["1m"],
        "active_instances": [
            {
                "strategy_instance_id": "unit",
                "strategy_name": "sma_with_filter",
                "parameter_source": "runtime_strategy_spec",
                "legacy_compatibility_used": False,
                "runtime_decision_request_hash": "sha256:unit-request",
                "runtime_decision_request_hash_scope": "run_start_blueprint_through_ts_null",
            }
        ],
        "strategy_instance_profile_bindings": [],
        "execution_config_hash": "sha256:unit-execution",
        "risk_config_hash": "sha256:unit-risk",
    }
    payload["runtime_strategy_set_manifest_hash"] = sha256_prefixed(payload)
    return payload


class _NoSubmitBroker:
    """Deterministic broker double for runtime failure scenarios.

    It deliberately has no order-submission method.  A test that reaches a
    submit path therefore fails rather than issuing a real broker request.
    """

    def get_open_orders(self, **_kwargs: object) -> list[object]:
        return []

    def get_balance(self):
        from bithumb_bot.broker.base import BrokerBalance

        return BrokerBalance(
            cash_available=100_000.0,
            cash_locked=0.0,
            asset_available=0.0,
            asset_locked=0.0,
        )


class _NoopNotifications:
    """In-memory notifier used so failure tests cannot contact a transport."""

    def send_event(self, _event: object, **_fields: object) -> None:
        return None

    def send_message(self, _message: str) -> None:
        return None


@dataclass
class PreparedRuntimeLoop:
    """Operation-owned, one-cycle runtime fixture for safety failure tests."""

    runner: Runner
    marked_recovery_required: int = 0


def prepare_runtime_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_order_created_ts: int | None = None,
) -> PreparedRuntimeLoop:
    """Prepare one deterministic live-dry-run cycle without external side effects."""
    db_path = Path(settings.DB_PATH).resolve()
    set_live_runtime_paths(monkeypatch, base_dir=db_path.parent / "live-runtime", db_path=db_path)

    for name, value in {
        "MODE": "live",
        "LIVE_DRY_RUN": True,
        "LIVE_REAL_ORDER_ARMED": False,
        "STRATEGY_NAME": "sma_with_filter",
        "INTERVAL": "1m",
        "KILL_SWITCH": False,
        "KILL_SWITCH_LIQUIDATE": False,
        "MAX_ORDER_KRW": 100_000.0,
        "MAX_DAILY_LOSS_KRW": 50_000.0,
        "MAX_DAILY_ORDER_COUNT": 10,
        "MAX_OPEN_ORDER_AGE_SEC": 5,
        "OPEN_ORDER_RECONCILE_MIN_INTERVAL_SEC": 30,
        "MARKET_RUNTIME_REGISTRY_REFRESH_INTERVAL_SEC": 0,
    }.items():
        object.__setattr__(settings, name, value)

    runtime_state.enable_trading()
    runtime_state.set_error_count(0)
    runtime_state.set_startup_gate_reason(None)
    runtime_state.reset_candle_processing_state()

    now_ms = int(time.time() * 1000)
    conn = ensure_db(str(db_path))
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO candles(pair, interval, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (settings.PAIR, settings.INTERVAL, now_ms - 65_000, 100.0, 100.0, 100.0, 100.0, 1.0),
        )
        if open_order_created_ts is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO orders(
                    client_order_id, exchange_order_id, status, side, price,
                    qty_req, qty_filled, created_ts, updated_ts, last_error
                ) VALUES (?, NULL, 'NEW', 'BUY', 100.0, 1.0, 0.0, ?, ?, NULL)
                """,
                ("operation-runtime-stale-order", int(open_order_created_ts), int(open_order_created_ts)),
            )
        conn.commit()
    finally:
        conn.close()

    notifications = _NoopNotifications()
    app = create_default_runtime_app(settings)
    tracker = PreparedRuntimeLoop(runner=Runner(app))

    def mark_recovery_required(reason: str, marked_at_ms: int) -> int:
        from bithumb_bot.runtime_data_access import mark_open_orders_recovery_required

        count = mark_open_orders_recovery_required(reason, marked_at_ms)
        tracker.marked_recovery_required += count
        return count

    import bithumb_bot.recovery as recovery_module

    app = replace(
        app,
        clock=lambda: time.time(),
        scheduler=SimpleNamespace(sleep=lambda _seconds: None),
        broker_factory=_NoSubmitBroker,
        market_sync=lambda quiet=True: None,
        notification_service=notifications,
        notification_adapter=app.notification_adapter.__class__(notifications),
        validate_market_runtime=lambda _cfg: None,
        interval_parser=lambda _interval: 60,
        runtime_strategy_set_manifest_provider=unit_runtime_strategy_set_manifest,
        open_order_snapshot=lambda timestamp_ms: __import__(
            "bithumb_bot.runtime_data_access", fromlist=["open_order_snapshot"]
        ).open_order_snapshot(timestamp_ms),
        mark_open_orders_recovery_required=mark_recovery_required,
        reconcile_with_broker=lambda broker: recovery_module.reconcile_with_broker(broker),
    )
    app = replace(
        app,
        safety_controller=replace(
            app.safety_controller,
            legacy_cancel_open_orders=lambda broker, reason: bool(
                __import__("bithumb_bot.compat.engine_legacy", fromlist=["_attempt_open_order_cancellation"])
                ._attempt_open_order_cancellation(broker, reason)
            ),
        ),
    )
    tracker.runner = Runner(app)
    tracker.runner._started = True
    tracker.runner.broker = _NoSubmitBroker()
    tracker.runner.runtime_checkpoint = RuntimeCheckpoint(symbol=settings.PAIR, interval=settings.INTERVAL)
    tracker.runner.runtime_events = RuntimeOperatorEventComposer(settings.PAIR)
    tracker.runner.runtime_strategy_set = SimpleNamespace(source="operation-test")
    return tracker


def run_one_runtime_cycle(
    loop: PreparedRuntimeLoop,
    *,
    enforce_startup_gate: bool = True,
) -> object | None:
    """Execute exactly one prepared cycle, including fail-closed startup gating."""
    gate_reason = evaluate_startup_safety_gate() if enforce_startup_gate else None
    if gate_reason:
        decision = loop.runner.container.safety_controller.evaluate_halt(
            HaltReason("STARTUP_SAFETY_GATE", gate_reason),
            unresolved=True,
        )
        loop.runner.container.safety_controller.apply(decision)
        return None
    return loop.runner.run_one_cycle()
