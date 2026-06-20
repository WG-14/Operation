from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bithumb_bot.config import settings
from bithumb_bot.db_core import ensure_db
from bithumb_bot.live_pipeline_smoke import (
    LivePipelineSmokeError,
    LivePipelineSmokeExecutionService,
    _readiness_from_broker,
    run_live_pipeline_smoke,
    validate_live_pipeline_smoke_request,
)
from bithumb_bot.live_pipeline_smoke_authority import (
    LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
    build_live_pipeline_smoke_authority_payload,
)
from bithumb_bot.storage_io import write_json_atomic


class _Broker:
    qty = 0.0

    def apply_fill(self, *, side: str, qty: float) -> None:
        if side == "BUY":
            self.qty += qty
        else:
            self.qty = max(0.0, self.qty - qty)

    def get_open_orders(self):
        return []


def _patch_settings(monkeypatch, db_path):
    old = {}
    for name, value in {
        "MODE": "live",
        "LIVE_DRY_RUN": False,
        "LIVE_REAL_ORDER_ARMED": True,
        "KILL_SWITCH": False,
        "PAIR": "KRW-BTC",
        "DB_PATH": str(db_path),
        "BITHUMB_API_KEY": "account",
    }.items():
        old[name] = getattr(settings, name)
        object.__setattr__(settings, name, value)
    return old


def _restore_settings(old):
    for name, value in old.items():
        object.__setattr__(settings, name, value)


def _authority(tmp_path, db_path):
    path = tmp_path / "authority.json"
    payload = build_live_pipeline_smoke_authority_payload(
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        market="KRW-BTC",
        db_path=str(db_path),
        account_key="account",
        code_commit_sha="unavailable",
    )
    write_json_atomic(path, payload)
    return path


def test_fake_broker_executes_five_round_trips(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "live.sqlite"
    old = _patch_settings(monkeypatch, db_path)
    try:
        conn = ensure_db(str(db_path))
        broker = _Broker()
        authority = _authority(tmp_path, db_path)
        monkeypatch.setattr("bithumb_bot.live_pipeline_smoke.runtime_code_provenance", lambda: {"commit_sha": "unavailable"})
        monkeypatch.setattr("bithumb_bot.live_pipeline_smoke.validate_live_pipeline_smoke_start_preflight", lambda **_kwargs: None)

        service = LivePipelineSmokeExecutionService(broker=broker)
        payload = run_live_pipeline_smoke(
            conn=conn,
            broker=broker,
            cycles=5,
            max_orders=10,
            max_notional_krw=10_000.0,
            yes=True,
            authority_path=str(authority),
            confirm=LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
            execution_service=service,
            readiness_provider=lambda: _readiness_from_broker(broker),
            post_trade_reconcile=lambda: None,
            run_id="lps_test",
        )

        assert payload["status"] == "passed"
        assert payload["orders_submitted"] == 10
        assert payload["buy_submitted"] == 5
        assert payload["sell_submitted"] == 5
        assert len(payload["rounds"]) == 5
        assert payload["final"]["broker_qty"] == 0.0
        assert conn.execute("SELECT COUNT(*) FROM strategy_decisions WHERE strategy_name='operator_live_pipeline_smoke'").fetchone()[0] == 10
        assert len(service.submissions) == 10
    finally:
        _restore_settings(old)


def test_failure_after_step_prevents_next_step(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "live.sqlite"
    old = _patch_settings(monkeypatch, db_path)
    try:
        conn = ensure_db(str(db_path))
        broker = _Broker()
        authority = _authority(tmp_path, db_path)
        monkeypatch.setattr("bithumb_bot.live_pipeline_smoke.runtime_code_provenance", lambda: {"commit_sha": "unavailable"})
        monkeypatch.setattr("bithumb_bot.live_pipeline_smoke.validate_live_pipeline_smoke_start_preflight", lambda **_kwargs: None)

        service = LivePipelineSmokeExecutionService(broker=broker, fail_at_step=1)
        payload = run_live_pipeline_smoke(
            conn=conn,
            broker=broker,
            cycles=5,
            max_orders=10,
            max_notional_krw=10_000.0,
            yes=True,
            authority_path=str(authority),
            confirm=LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
            execution_service=service,
            readiness_provider=lambda: _readiness_from_broker(broker),
            post_trade_reconcile=lambda: None,
            run_id="lps_test",
        )

        assert payload["status"] == "failed"
        assert payload["orders_submitted"] == 1
        assert len(service.submissions) == 1
    finally:
        _restore_settings(old)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"apply": True, "yes": False, "authority_path": "/tmp/a", "confirm": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN},
        {"apply": True, "yes": True, "authority_path": None, "confirm": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN},
        {"apply": True, "yes": True, "authority_path": "/tmp/a", "confirm": "wrong"},
        {"apply": True, "yes": True, "authority_path": "/tmp/a", "confirm": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN, "cycles": 4},
        {"apply": True, "yes": True, "authority_path": "/tmp/a", "confirm": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN, "max_orders": 9},
    ],
)
def test_apply_regression_bounds_rejected(kwargs) -> None:
    base = {
        "apply": True,
        "yes": True,
        "cycles": 5,
        "max_orders": 10,
        "max_notional_krw": 10_000.0,
        "authority_path": "/tmp/a",
        "confirm": LIVE_PIPELINE_SMOKE_CONFIRMATION_TOKEN,
        "mode": "live",
    }
    base.update(kwargs)
    with pytest.raises(LivePipelineSmokeError):
        validate_live_pipeline_smoke_request(**base)
