from __future__ import annotations

import pytest

from operation.db_core import ensure_db
from operation.lifecycle import apply_fill_lifecycle
from operation.runtime_scope import derive_risk_scope_id, require_risk_scope_reset_authority, strategy_revision_id
from operation.risk_contract import RiskPolicy
from operation.risk_policy_engine import RiskPolicyEngine
from operation.strategy_risk_state import StrategyRiskStateProvider


def _payload(**overrides: object) -> dict[str, object]:
    payload = {
        "strategy_name": "sma_with_filter",
        "strategy_instance_id": "old",
        "pair": "KRW-BTC",
        "interval": "1m",
        "runtime_contract_hash": "sha256:" + "1" * 64,
        "approved_profile_hash": "sha256:" + "2" * 64,
        "strategy_parameters_hash": "sha256:" + "3" * 64,
        "risk_policy_hash": "sha256:" + "4" * 64,
        "risk_capital_basis": "fixed_observation_notional",
        "risk_capital_krw": 100_000,
    }
    payload.update(overrides)
    return payload


def test_non_economic_runtime_contract_change_preserves_risk_scope_id() -> None:
    old = _payload(strategy_instance_id="64fb", runtime_contract_hash="sha256:" + "1" * 64)
    new = _payload(strategy_instance_id="cabccc", runtime_contract_hash="sha256:" + "9" * 64)

    assert strategy_revision_id(old) != strategy_revision_id(new)
    assert derive_risk_scope_id(old) == derive_risk_scope_id(new)


def test_risk_scope_reset_requires_explicit_authority() -> None:
    with pytest.raises(ValueError, match="risk_scope_reset_authority_required"):
        require_risk_scope_reset_authority(
            previous=_payload(risk_capital_krw=100_000),
            current=_payload(risk_capital_krw=200_000),
        )


def test_strategy_revision_change_does_not_drop_lifecycle_history() -> None:
    old = _payload(strategy_instance_id="64fb")
    new = _payload(strategy_instance_id="cabccc")

    assert derive_risk_scope_id(old) == derive_risk_scope_id(new)


def _seed_loss_lifecycle(conn, *, old_instance_id: str, risk_scope_id: str) -> None:
    decision_id = conn.execute(
        """
        INSERT INTO strategy_decisions(decision_ts, strategy_name, signal, reason, candle_ts, market_price, context_json)
        VALUES (?, 'sma_with_filter', 'BUY', 'unit', ?, 100.0, ?)
        """,
        (
            1_800_000_000_000,
            1_800_000_000_000,
            (
                '{"strategy_name":"sma_with_filter",'
                f'"strategy_instance_id":"{old_instance_id}",'
                '"pair":"KRW-BTC","interval":"1m",'
                f'"risk_scope_id":"{risk_scope_id}"'
                "}"
            ),
        ),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO trade_lifecycles(
            pair, entry_trade_id, exit_trade_id, entry_client_order_id, exit_client_order_id,
            entry_ts, exit_ts, matched_qty, entry_price, exit_price, gross_pnl, fee_total,
            net_pnl, holding_time_sec, strategy_name, strategy_instance_id,
            owner_strategy_instance_id, owner_risk_scope_id, risk_scope_id, entry_decision_id
        ) VALUES ('KRW-BTC', 1, 2, 'entry', 'exit', ?, ?, 1, 100, 90, -10, 0, -10, 60,
            'sma_with_filter', ?, ?, ?, ?, ?)
        """,
        (
            1_800_000_000_000,
            1_800_000_060_000,
            old_instance_id,
            old_instance_id,
            risk_scope_id,
            risk_scope_id,
            decision_id,
        ),
    )
    conn.commit()


def _seed_scope_decision(conn, *, instance_id: str, risk_scope_id: str, ts: int = 1_800_000_000_000) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO strategy_decisions(decision_ts, strategy_name, signal, reason, candle_ts, market_price, context_json)
            VALUES (?, 'sma_with_filter', 'BUY', 'unit', ?, 100.0, ?)
            """,
            (
                ts,
                ts,
                (
                    '{"strategy_name":"sma_with_filter",'
                    f'"strategy_instance_id":"{instance_id}",'
                    '"pair":"KRW-BTC","interval":"1m",'
                    f'"risk_scope_id":"{risk_scope_id}"'
                    "}"
                ),
            ),
        ).lastrowid
    )


def test_new_lifecycle_insert_records_actual_risk_scope_id(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "lifecycle-risk-scope.sqlite"))
    decision_id = _seed_scope_decision(conn, instance_id="old-instance", risk_scope_id="scope-a")
    apply_fill_lifecycle(
        conn,
        side="BUY",
        pair="KRW-BTC",
        trade_id=1,
        client_order_id="entry",
        fill_id="entry-fill",
        fill_ts=1_800_000_000_000,
        price=100.0,
        qty=1.0,
        fee=0.0,
        strategy_name="sma_with_filter",
        entry_decision_id=decision_id,
    )
    apply_fill_lifecycle(
        conn,
        side="SELL",
        pair="KRW-BTC",
        trade_id=2,
        client_order_id="exit",
        fill_id="exit-fill",
        fill_ts=1_800_000_060_000,
        price=90.0,
        qty=1.0,
        fee=0.0,
        strategy_name="sma_with_filter",
        entry_decision_id=decision_id,
        exit_decision_id=decision_id,
    )

    row = conn.execute(
        """
        SELECT owner_strategy_instance_id, owner_risk_scope_id, risk_scope_id, risk_scope_source
        FROM trade_lifecycles
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()

    assert row["owner_strategy_instance_id"] == "old-instance"
    assert row["owner_risk_scope_id"] == "scope-a"
    assert row["risk_scope_id"] == "scope-a"
    assert row["risk_scope_source"] == "decision_context_risk_scope_id"


def test_strategy_revision_change_preserves_loss_order_trade_and_open_exposure_history(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "revision-continuity.sqlite"))
    decision_id = _seed_scope_decision(conn, instance_id="old-instance", risk_scope_id="scope-a")
    _seed_loss_lifecycle(conn, old_instance_id="old-instance", risk_scope_id="scope-a")
    conn.execute(
        """
        INSERT INTO orders(
            client_order_id, status, side, pair, order_type, price, qty_req, qty_filled,
            strategy_name, strategy_instance_id, entry_decision_id, created_ts, updated_ts
        ) VALUES ('entry-order', 'filled', 'BUY', 'KRW-BTC', 'limit', 100, 1, 1,
            'sma_with_filter', 'old-instance', ?, ?, ?)
        """,
        (decision_id, 1_800_000_030_000, 1_800_000_030_000),
    )
    conn.execute(
        """
        INSERT INTO trades(
            ts, pair, interval, side, price, qty, fee, cash_after, asset_after,
            client_order_id, strategy_name, entry_decision_id
        ) VALUES (?, 'KRW-BTC', '1m', 'BUY', 100, 1, 0, 0, 1,
            'entry-order', 'sma_with_filter', ?)
        """,
        (1_800_000_040_000, decision_id),
    )
    conn.execute(
        """
        INSERT INTO open_position_lots(
            pair, entry_trade_id, entry_client_order_id, entry_fill_id, entry_ts,
            entry_price, qty_open, executable_lot_count, position_state,
            strategy_name, strategy_instance_id, entry_decision_id, entry_decision_linkage
        ) VALUES ('KRW-BTC', 10, 'entry-order', 'fill', ?, 100, 1, 1, 'open_exposure',
            'sma_with_filter', 'old-instance', ?, 'direct')
        """,
        (1_800_000_040_000, decision_id),
    )
    conn.commit()

    snapshot = StrategyRiskStateProvider(conn).snapshot(
        strategy_instance_id="new-instance",
        strategy_name="sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        as_of_ts_ms=1_800_000_120_000,
        mark_price=100.0,
        policy=RiskPolicy(
            max_daily_loss_krw=1.0,
            max_daily_order_count=10,
            max_trade_count_per_day=10,
            source="unit",
        ),
        enforced=True,
        risk_scope_id="scope-a",
    )

    assert snapshot.loss_today == pytest.approx(10.0)
    assert snapshot.daily_order_count == 1
    assert snapshot.daily_trade_count == 1
    assert snapshot.current_asset_qty == pytest.approx(1.0)


def test_risk_state_evidence_marks_order_trade_asset_scope_as_risk_scope(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "risk-evidence-scope.sqlite"))
    _seed_scope_decision(conn, instance_id="old-instance", risk_scope_id="scope-a")

    snapshot = StrategyRiskStateProvider(conn).snapshot(
        strategy_instance_id="new-instance",
        strategy_name="sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        as_of_ts_ms=1_800_000_120_000,
        mark_price=100.0,
        risk_scope_id="scope-a",
    )
    derivation = snapshot.evidence["state_derivation"]

    assert snapshot.evidence["scope"] == "risk_scope"
    assert "risk_scope_via_decision_ids" in snapshot.evidence
    assert derivation["daily_order_count"]["scope"] == "risk_scope_via_decision_ids"
    assert derivation["daily_trade_count"]["scope"] == "risk_scope_via_decision_ids"
    assert derivation["current_asset_qty"]["scope"] == "risk_scope_via_decision_ids"


def test_loss_today_uses_risk_scope_id_not_strategy_instance_id(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "loss-scope.sqlite"))
    _seed_loss_lifecycle(conn, old_instance_id="old-instance", risk_scope_id="scope-a")

    snapshot = StrategyRiskStateProvider(conn).snapshot(
        strategy_instance_id="new-instance",
        strategy_name="sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        as_of_ts_ms=1_800_000_120_000,
        mark_price=100.0,
        policy=RiskPolicy(max_daily_loss_krw=1.0, source="unit"),
        enforced=True,
        risk_scope_id="scope-a",
    )

    assert snapshot.loss_today == pytest.approx(10.0)
    assert snapshot.evidence["state_derivation"]["loss_today"]["scope"] == "risk_scope"


def test_cooldown_uses_risk_scope_id_not_strategy_instance_id(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "cooldown-scope.sqlite"))
    _seed_loss_lifecycle(conn, old_instance_id="old-instance", risk_scope_id="scope-a")
    policy = RiskPolicy(cooldown_after_loss_min=15, source="unit")

    snapshot = StrategyRiskStateProvider(conn).snapshot(
        strategy_instance_id="new-instance",
        strategy_name="sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        as_of_ts_ms=1_800_000_120_000,
        mark_price=100.0,
        policy=policy,
        enforced=True,
        risk_scope_id="scope-a",
    )
    decision = RiskPolicyEngine(policy).evaluate_pre_decision(snapshot)

    assert snapshot.minutes_since_last_loss == pytest.approx(1.0)
    assert snapshot.evidence["state_derivation"]["minutes_since_last_loss"]["scope"] == "risk_scope"
    assert decision.reason_code == "COOLDOWN_AFTER_LOSS"
