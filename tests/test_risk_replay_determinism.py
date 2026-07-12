from __future__ import annotations

import json

import pytest

from bithumb_bot.canonical_decision import sha256_prefixed
from bithumb_bot.db_core import ensure_db
from bithumb_bot.risk_contract import RiskPolicy
from bithumb_bot.risk_layer_replay import build_risk_replay_input_artifact, verify_risk_layer_replay
from bithumb_bot.risk_policy_engine import RiskPolicyEngine
from bithumb_bot.strategy_risk_state import StrategyRiskStateProvider


def _persisted_strategy_fixture(
    conn,
    *,
    env_hash: str = "sha256:" + "b" * 64,
    include_tables: bool = True,
    with_execution: bool = True,
) -> tuple[int, int | None, dict[str, object]]:
    policy = RiskPolicy(max_daily_loss_krw=1000.0, source="unit")
    snapshot = StrategyRiskStateProvider(conn).snapshot(
        strategy_instance_id="instance-a",
        strategy_name="sma_with_filter",
        pair="KRW-BTC",
        interval="1m",
        as_of_ts_ms=1_800_000_000_000,
        mark_price=100.0,
        policy=policy,
        enforced=True,
        risk_scope_id="risk-a",
        db_snapshot_hash="sha256:" + "a" * 64,
        env_hash=env_hash,
        runtime_scope_id="runtime-a",
    )
    decision = RiskPolicyEngine(policy).evaluate_pre_decision(snapshot).as_dict()
    evidence = dict(decision["evidence"])
    if env_hash == "":
        evidence["env_hash"] = ""
        evidence.pop("replay_input_bundle_hash", None)
    if not include_tables:
        evidence.pop("included_tables_hashes", None)
        evidence.pop("replay_input_bundle_hash", None)
    if env_hash == "" or not include_tables:
        decision["evidence"] = evidence
        from bithumb_bot.risk_contract import build_risk_decision

        rebuilt_snapshot = type(snapshot)(
            **{
                **snapshot.as_dict(),
                "evidence": evidence,
            }
        )
        decision = build_risk_decision(
            evaluation_point="pre_decision",
            status="ALLOW",
            reason_code="OK",
            reason="ok",
            allowed_actions=("BUY", "SELL", "HOLD"),
            recommended_action=None,
            snapshot=rebuilt_snapshot,
            policy=policy,
            evidence=evidence,
        ).as_dict()
    replay_hash = str(dict(decision["evidence"]).get("replay_input_bundle_hash") or "")
    execution_plan_id = None
    if with_execution:
        bundle_hash = sha256_prefixed({"bundle": "unit", "replay_input_bundle_hash": replay_hash})
        conn.execute(
            """
            INSERT INTO runtime_strategy_decision_bundle(
                candle_ts, pair, interval, strategy_set_manifest_hash,
                bundle_hash, result_count, created_ts
            ) VALUES (?, 'KRW-BTC', '1m', 'manifest', ?, 1, ?)
            """,
            (1_800_000_000_000, bundle_hash, 1_800_000_000_000),
        )
        bundle_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        allocation_hash = sha256_prefixed({"allocation": "unit", "bundle_hash": bundle_hash})
        conn.execute(
            """
            INSERT INTO portfolio_allocation_decision(
                bundle_id, allocation_decision_hash, allocation_input_hash,
                allocator_config_hash, strategy_contribution_hash, selected_signal,
                authoritative, primary_block_reason, reason,
                conflict_resolution_json, allocation_decision_json
            ) VALUES (?, ?, 'input', 'allocator', 'contribution', 'HOLD',
                1, '', 'unit', '{}', '{}')
            """,
            (bundle_id, allocation_hash),
        )
        allocation_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        submit_payload = {
            "side": "HOLD",
            "qty": 0.0,
            "source": "target_delta",
            "authority": "unit",
            "submit_expected": False,
            "final_action": "HOLD",
            "block_reason": "unit",
            "replay_input_bundle_hash": replay_hash,
            "strategy_risk_decision_hash": decision["risk_decision_hash"],
        }
        submit_payload["submit_plan_hash"] = sha256_prefixed(
            {
                "side": "HOLD",
                "qty": 0.0,
                "source": "target_delta",
                "authority": "unit",
                "submit_expected": False,
                "final_action": "HOLD",
                "block_reason": "unit",
            }
        )
        conn.execute(
            """
            INSERT INTO execution_plan(
                allocation_id, portfolio_target_hash, execution_plan_bundle_hash,
                execution_submit_plan_hash, submit_plan_side, submit_plan_qty,
                submit_expected, final_action, block_reason, status,
                execution_plan_bundle_json, execution_submit_plan_json
            ) VALUES (?, 'target', ?, ?, 'HOLD', 0, 0, 'HOLD', 'unit', 'planned', '{}', ?)
            """,
            (
                allocation_id,
                bundle_hash,
                submit_payload["submit_plan_hash"],
                json.dumps(submit_payload, sort_keys=True),
            ),
        )
        execution_plan_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    context = {
        "strategy_risk_decision": decision,
        "strategy_preferences": [
            {
                "strategy_risk_decision": decision,
                "strategy_risk_profile": {
                    "risk_policy": policy.as_dict(),
                    "risk_enforcement_mode": "enforced",
                },
            }
        ],
    }
    decision_id = int(
        conn.execute(
            """
            INSERT INTO strategy_decisions(
                decision_ts, strategy_name, signal, reason, candle_ts, market_price,
                execution_plan_id, context_json
            ) VALUES (?, 'sma_with_filter', 'HOLD', 'unit', ?, 100.0, ?, ?)
            """,
            (
                1_800_000_000_000,
                1_800_000_000_000,
                execution_plan_id,
                json.dumps(context, sort_keys=True),
            ),
        ).lastrowid
    )
    conn.commit()
    return decision_id, execution_plan_id, decision


def test_same_snapshot_replays_same_risk_decision_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "replay.sqlite"))
    kwargs = {
        "db_snapshot_hash": "sha256:" + "a" * 64,
        "env_hash": "sha256:" + "b" * 64,
        "runtime_scope_id": "scope",
        "risk_scope_id": "risk",
        "candle_ts": 1,
        "mark_price": 100.0,
    }

    first = build_risk_replay_input_artifact(conn, **kwargs)
    second = build_risk_replay_input_artifact(conn, **kwargs)

    assert first["risk_decision_hash"] == second["risk_decision_hash"]


def test_same_snapshot_replays_same_execution_plan_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "replay-execution.sqlite"))
    kwargs = {
        "db_snapshot_hash": "sha256:" + "a" * 64,
        "env_hash": "sha256:" + "b" * 64,
        "runtime_scope_id": "scope",
        "risk_scope_id": "risk",
        "candle_ts": 1,
        "mark_price": 100.0,
    }

    first = build_risk_replay_input_artifact(conn, **kwargs)
    second = build_risk_replay_input_artifact(conn, **kwargs)

    assert first["execution_plan_hash"] == second["execution_plan_hash"]


def test_db_history_change_changes_risk_input_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "replay-change.sqlite"))
    kwargs = {
        "db_snapshot_hash": "sha256:" + "a" * 64,
        "env_hash": "sha256:" + "b" * 64,
        "runtime_scope_id": "scope",
        "risk_scope_id": "risk",
        "candle_ts": 1,
        "mark_price": 100.0,
    }
    before = build_risk_replay_input_artifact(conn, **kwargs)
    conn.execute(
        """
        INSERT INTO trade_lifecycles(
            pair, entry_trade_id, exit_trade_id, entry_client_order_id, exit_client_order_id,
            entry_ts, exit_ts, matched_qty, entry_price, exit_price, gross_pnl, fee_total,
            net_pnl, holding_time_sec
        ) VALUES ('KRW-BTC', 1, 2, 'e', 'x', 1, 2, 1, 100, 90, -10, 0, -10, 1)
        """
    )
    after = build_risk_replay_input_artifact(conn, **kwargs)

    assert before["risk_input_hash"] != after["risk_input_hash"]


def test_missing_snapshot_hash_fails_replay_contract(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "replay-missing.sqlite"))

    with pytest.raises(ValueError, match="risk_replay_db_snapshot_hash_missing"):
        build_risk_replay_input_artifact(
            conn,
            db_snapshot_hash="",
            env_hash="sha256:" + "b" * 64,
            runtime_scope_id="scope",
            risk_scope_id="risk",
            candle_ts=1,
            mark_price=100.0,
        )


def test_persisted_strategy_risk_decision_replays_same_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "persisted-risk.sqlite"))
    decision_id, _, decision = _persisted_strategy_fixture(conn, with_execution=False)

    report = verify_risk_layer_replay(conn, decision_id=decision_id)

    assert report["overall_status"] == "pass"
    assert report["layers"]["strategy"]["actual_decision_hash"] == decision["risk_decision_hash"]


def test_persisted_execution_plan_replays_same_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "persisted-execution.sqlite"))
    decision_id, execution_plan_id, decision = _persisted_strategy_fixture(conn)

    report = verify_risk_layer_replay(conn, decision_id=decision_id, execution_plan_id=execution_plan_id)

    assert report["overall_status"] == "pass"
    assert report["layers"]["execution_plan"]["input_hash"] == decision["evidence"]["replay_input_bundle_hash"]


def test_persisted_replay_fails_when_env_hash_missing(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "persisted-env-missing.sqlite"))
    decision_id, execution_plan_id, _ = _persisted_strategy_fixture(conn, env_hash="")

    report = verify_risk_layer_replay(conn, decision_id=decision_id, execution_plan_id=execution_plan_id)

    assert report["overall_status"] == "fail"
    assert "env_hash" in report["layers"]["strategy"]["mismatch_reason"]


def test_persisted_replay_fails_when_included_tables_hashes_missing(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "persisted-tables-missing.sqlite"))
    decision_id, execution_plan_id, _ = _persisted_strategy_fixture(conn, include_tables=False)

    report = verify_risk_layer_replay(conn, decision_id=decision_id, execution_plan_id=execution_plan_id)

    assert report["overall_status"] == "fail"
    assert "included_tables_hashes" in report["layers"]["strategy"]["mismatch_reason"]


def test_trade_lifecycle_change_changes_persisted_risk_input_hash(tmp_path) -> None:
    conn = ensure_db(str(tmp_path / "persisted-input-change.sqlite"))
    _, _, before_decision = _persisted_strategy_fixture(conn, with_execution=False)
    before_hash = before_decision["evidence"]["replay_input_bundle_hash"]
    conn.execute(
        """
        INSERT INTO trade_lifecycles(
            pair, entry_trade_id, exit_trade_id, entry_client_order_id, exit_client_order_id,
            entry_ts, exit_ts, matched_qty, entry_price, exit_price, gross_pnl, fee_total,
            net_pnl, holding_time_sec
        ) VALUES ('KRW-BTC', 1, 2, 'e', 'x', 1, 2, 1, 100, 90, -10, 0, -10, 1)
        """
    )
    _, _, after_decision = _persisted_strategy_fixture(conn, with_execution=False)

    assert before_hash != after_decision["evidence"]["replay_input_bundle_hash"]
