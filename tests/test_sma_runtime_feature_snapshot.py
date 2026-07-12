from __future__ import annotations

import socket
import time
from dataclasses import replace
from pathlib import Path

from operation.config import settings
from operation.db_core import ensure_db
from operation.operation_strategy.registry import resolve_operation_strategy_plugin
from operation.runtime_adapters.sma_with_filter import SmaWithFilterRuntimeDecisionAdapter
from operation.runtime_data_provider import RuntimeDataRequirementResolver, SQLiteRuntimeDataProvider
from operation.runtime_strategy_decision import _attach_runtime_feature_snapshot_metadata, _attach_runtime_request_metadata
from operation.runtime_strategy_set import (
    RuntimeDecisionRequestBuilder,
    RuntimeMarketScope,
    RuntimeStrategySet,
    RuntimeStrategySpec,
    validate_runtime_decision_result_provenance,
)


def _seed_candles(conn, *, count: int, latest_ts: int) -> tuple[int, ...]:
    timestamps: list[int] = []
    for index in range(count):
        ts = latest_ts - (count - 1 - index) * 60_000
        close = 100.0 + index * 0.1
        conn.execute(
            "INSERT INTO candles(ts,pair,interval,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
            (ts, "KRW-BTC", "1m", close, close + 1.0, close - 1.0, close, 1.0),
        )
        timestamps.append(ts)
    conn.commit()
    return tuple(timestamps)


def test_sma_plugin_projects_sqlite_snapshot_to_typed_runtime_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = resolve_operation_strategy_plugin("sma_with_filter")
    assert callable(plugin.runtime_feature_snapshot_builder)
    assert plugin.contract_payload()["runtime_feature_snapshot_builder_supported"] is True

    cfg = replace(
        settings,
        MODE="paper",
        PAIR="KRW-BTC",
        INTERVAL="1m",
        STRATEGY_NAME="sma_with_filter",
        OPERATION_APPROVAL_PATH="",
    )
    parameters = plugin.runtime_parameter_adapter.from_settings(cfg)  # type: ignore[union-attr]
    spec = RuntimeStrategySpec(
        "sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        parameters=parameters,
    )
    strategy_set = RuntimeStrategySet(
        strategies=(spec,),
        source="unit",
        market_scope=RuntimeMarketScope(pair="KRW-BTC", interval="1m"),
    )
    requirements = RuntimeDataRequirementResolver().resolve_for_strategy_set(strategy_set)
    required_rows = next(item.lookback_rows for item in requirements.required if item.name == "candles")
    latest_ts = ((int(time.time() * 1000) // 60_000) - 1) * 60_000

    conn = ensure_db(str(tmp_path / "paper.sqlite"))
    try:
        seeded_timestamps = _seed_candles(
            conn,
            count=int(required_rows) + 4,
            latest_ts=latest_ts,
        )
        request = RuntimeDecisionRequestBuilder(settings_obj=cfg).build_for_spec(
            spec,
            through_ts_ms=latest_ts,
        )
        monkeypatch.setattr(
            socket.socket,
            "connect",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call")),
        )

        feature_snapshot = SQLiteRuntimeDataProvider(conn).snapshot(request, requirements)
        assert feature_snapshot is not None
        payload = feature_snapshot.as_dict()
        feature_payload = payload["feature_payload"]
        assert feature_payload["capabilities"]["candles"]["rows"]
        projection = feature_payload["sma_with_filter"]
        for field in (
            "candles",
            "position_context",
            "position_snapshot",
            "position_state",
            "order_rules",
            "fee_authority",
        ):
            assert projection[field]

        result = SmaWithFilterRuntimeDecisionAdapter().decide_feature_snapshot(
            request,
            feature_snapshot,
        )
        assert result is not None
        assert result.decision.final_signal in {"BUY", "SELL", "HOLD"}
        assert result.candle_ts in seeded_timestamps
        _attach_runtime_feature_snapshot_metadata(result, feature_snapshot)
        _attach_runtime_request_metadata(result, request)
        validate_runtime_decision_result_provenance(result, request)
    finally:
        conn.close()


def test_sma_provider_returns_no_snapshot_for_actual_insufficient_history(tmp_path: Path) -> None:
    cfg = replace(settings, MODE="paper", PAIR="KRW-BTC", INTERVAL="1m")
    plugin = resolve_operation_strategy_plugin("sma_with_filter")
    parameters = plugin.runtime_parameter_adapter.from_settings(cfg)  # type: ignore[union-attr]
    spec = RuntimeStrategySpec(
        "sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        parameters=parameters,
    )
    strategy_set = RuntimeStrategySet(
        strategies=(spec,),
        source="unit",
        market_scope=RuntimeMarketScope(pair="KRW-BTC", interval="1m"),
    )
    requirements = RuntimeDataRequirementResolver().resolve_for_strategy_set(strategy_set)
    required_rows = next(item.lookback_rows for item in requirements.required if item.name == "candles")
    latest_ts = ((int(time.time() * 1000) // 60_000) - 1) * 60_000
    conn = ensure_db(str(tmp_path / "paper.sqlite"))
    try:
        _seed_candles(conn, count=int(required_rows) - 1, latest_ts=latest_ts)
        request = RuntimeDecisionRequestBuilder(settings_obj=cfg).build_for_spec(
            spec,
            through_ts_ms=latest_ts,
        )
        assert SQLiteRuntimeDataProvider(conn).snapshot(request, requirements) is None
    finally:
        conn.close()
