from __future__ import annotations

import inspect
import sqlite3
from types import SimpleNamespace

from bithumb_bot.db_core import ensure_schema
from bithumb_bot.runtime.cycle_pipeline import RuntimeCyclePipeline
from bithumb_bot.runtime.data_cycle_preflight import RuntimeDataCyclePreflight
from bithumb_bot.runtime_decision_contract import RuntimeStrategyPolicyHashes
from bithumb_bot.runtime_strategy_set import (
    RuntimeStrategyDecisionCollector,
    RuntimeStrategySet,
    RuntimeStrategySpec,
)
from bithumb_bot.strategy_policy_contract import PositionSnapshot, StrategyDecisionV2


class _RuntimeResult:
    def __init__(self, strategy_name: str, candle_ts: int = 1_700_000_180_000) -> None:
        self.decision = StrategyDecisionV2(
            strategy_name=strategy_name, raw_signal="HOLD", raw_reason="unit",
            entry_signal="HOLD", entry_reason="unit", exit_signal="HOLD", exit_reason="unit",
            final_signal="HOLD", final_reason="unit", blocked_filters=(), entry_blocked=False,
            entry_block_reason=None, exit_rule=None, exit_evaluations=(),
            protective_exit_overrode_entry=False, exit_filter_suppression_prevented=False,
            position_snapshot=PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False),
            execution_intent=None, entry_decision=object(), trace={"strategy_name": strategy_name},
            policy_hash="sha256:pure", policy_contract_hash="sha256:contract",
            policy_input_hash="sha256:input", policy_decision_hash="sha256:decision",
        )
        self.base_context = {"market_price": 10.0, "last_close": 10.0}
        self.candle_ts = candle_ts
        self.market_price = 10.0
        self.replay_fingerprint = {"schema_version": 1, "candle_ts": candle_ts}
        self.boundary = {"decision_boundary_phase": "unit"}
        self.policy_hashes = RuntimeStrategyPolicyHashes({
            "pure_policy_hash": "sha256:pure", "policy_contract_hash": "sha256:contract",
            "policy_input_hash": "sha256:input", "policy_decision_hash": "sha256:decision",
        })

    def as_legacy_dict(self) -> dict[str, object]:
        return dict(self.base_context)


def _adapter_resolver(adapters: dict[str, object]):
    def resolve(strategy_name: str):
        adapter = adapters.get(str(strategy_name).strip().lower())
        return adapter() if isinstance(adapter, type) else adapter

    return resolve


def _canary_spec() -> RuntimeStrategySpec:
    return RuntimeStrategySpec(
        "safe_hold",
        parameters={},
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    for idx in range(4):
        ts = 1_700_000_000_000 + idx * 60_000
        close = 10.0 + idx
        conn.execute(
            "INSERT OR REPLACE INTO candles(ts, pair, interval, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, "KRW-BTC", "1m", close, close, close, close, 1.0),
        )
    conn.commit()
    return conn


def test_data_cycle_preflight_reports_closed_candle_and_strategy_scope_hash() -> None:
    preflight = RuntimeDataCyclePreflight(
        status="PASS",
        reason_code=None,
        latest_candle_ts=100,
        latest_close=10.0,
        closed_candle_ts=100,
        incomplete_candle_ts=160,
        candle_age_sec=2.0,
        stale_cutoff_sec=120,
        closed_candle_allowed=True,
        runtime_data_availability_report_hash="sha256:availability",
        coverage_by_scope={"scope": {"candles": {"status": "PASS"}}},
        selected_candle_by_scope={"scope": {"selected_ts": 100}},
        freshness_by_scope={"scope": {"status": "PASS"}},
    )

    payload = preflight.as_dict()
    assert payload["closed_candle_allowed"] is True
    assert payload["runtime_data_availability_report_hash"] == "sha256:availability"
    assert payload["coverage_by_scope"] == {"scope": {"candles": {"status": "PASS"}}}
    assert payload["decision_hash"].startswith("sha256:")


def test_stale_candle_blocks_before_decision_gateway(monkeypatch) -> None:
    calls = {"decision": 0}
    preflight = RuntimeDataCyclePreflight(
        status="FAIL",
        reason_code="stale_candle_detected",
        latest_candle_ts=100,
        latest_close=10.0,
        closed_candle_ts=None,
        incomplete_candle_ts=None,
        candle_age_sec=121.0,
        stale_cutoff_sec=120,
        closed_candle_allowed=False,
        runtime_data_availability_report_hash=None,
        coverage_by_scope={},
        selected_candle_by_scope={},
        freshness_by_scope={},
    )

    class _Provider:
        def __init__(self, **_kwargs) -> None:
            pass

        def evaluate(self, **_kwargs):
            return preflight

    monkeypatch.setattr("bithumb_bot.runtime.cycle_pipeline.RuntimeDataCyclePreflightProvider", _Provider)

    class _DecisionCoordinator:
        def decide_cycle(self, **_kwargs):
            calls["decision"] += 1
            raise AssertionError("decision gateway should not be called for stale candle")

    events = SimpleNamespace(event=lambda name, **fields: {"event_hash": f"sha256:{name}", **fields})
    container = SimpleNamespace(
        settings_obj=SimpleNamespace(PAIR="KRW-BTC", INTERVAL="1m"),
        interval_parser=lambda _interval: 60,
        clock=lambda: 1_700_000_200.0,
        notification_adapter=SimpleNamespace(send_event=lambda _event: None),
        decision_coordinator=_DecisionCoordinator(),
    )
    runner = SimpleNamespace(
        container=container,
        runtime_checkpoint=object(),
        runtime_events=events,
        runtime_strategy_set=RuntimeStrategySet(source="unit", strategies=(_canary_spec(),)),
        fail_count=0,
        max_fails=5,
        _record_artifact=lambda cycle_id, **kwargs: {"cycle_id": cycle_id, **kwargs},
    )

    artifact = RuntimeCyclePipeline(runner).run_once()

    assert calls["decision"] == 0
    assert artifact["cycle_id"] == "skip:stale_candle"


def test_strategy_scope_preflight_failure_returns_single_reason_code() -> None:
    source = inspect.getsource(RuntimeCyclePipeline.run_once)
    assert 'preflight.reason_code == "runtime_data_preflight_failed"' in source
    assert "skip:runtime_data_preflight_failed" in source


def test_decision_bundle_references_cycle_preflight_hash() -> None:
    preflight_hash = "sha256:cycle-preflight"

    class _Adapter:
        strategy_name = "safe_hold"

        def decide_feature_snapshot(self, request, feature_snapshot):
            del request, feature_snapshot
            return _RuntimeResult(self.strategy_name)

        def typed_authority_required(self) -> bool:
            return True

    bundle = RuntimeStrategyDecisionCollector(
        adapter_resolver=_adapter_resolver({"safe_hold": _Adapter()}),
    ).collect(
        _conn(),
        RuntimeStrategySet(source="unit", strategies=(_canary_spec(),)),
        through_ts_ms=1_700_000_180_000,
        runtime_data_cycle_preflight_hash=preflight_hash,
    )

    assert bundle is not None
    payload = bundle.as_dict()
    assert payload["runtime_data_cycle_preflight_hash"] == preflight_hash
    assert payload["results"][0]["runtime_data_cycle_preflight_hash"] == preflight_hash


def test_pipeline_shell_does_not_calculate_stale_candle_cutoff() -> None:
    source = inspect.getsource(RuntimeCyclePipeline.run_once)
    assert "candle_age_sec > stale_cutoff_sec" not in source
    assert "c.candle_reader(" not in source
    assert "c.closed_candle_selector(" not in source
    assert "runtime_state.set_last_candle_observation(" not in source
