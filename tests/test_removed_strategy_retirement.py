from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from bithumb_bot.db_core import ensure_schema
from bithumb_bot.run_lock import acquire_run_lock


sys.path.insert(0, str(Path("tools/migrations").resolve()))
import _offline_retirement as retirement  # noqa: E402


def _paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    roots = {key: tmp_path / value for key, value in {
        "ENV_ROOT": "env", "RUN_ROOT": "run", "DATA_ROOT": "data", "LOG_ROOT": "logs",
        "BACKUP_ROOT": "backup", "ARCHIVE_ROOT": "archive",
    }.items()}
    monkeypatch.setenv("MODE", "paper")
    monkeypatch.delenv("DB_PATH", raising=False)
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value.resolve()))
    return roots["DATA_ROOT"] / "paper" / "trades" / "paper.sqlite", roots["BACKUP_ROOT"] / "paper" / "db" / "retirement.sqlite"


def _legacy_db(db_path: Path, *, target: bool = False, asset_qty: float = 0.0) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.execute("INSERT INTO portfolio(id, cash_krw, asset_qty) VALUES (1, 1, ?)", (asset_qty,))
        for column in sorted(retirement.RETIRED_ORDER_COLUMNS):
            conn.execute(f"ALTER TABLE orders ADD COLUMN {retirement._quote(column)} TEXT")
        conn.execute("""CREATE TABLE h74_cycle_state (
            cycle_id TEXT PRIMARY KEY, authority_hash TEXT NOT NULL, strategy_instance_id TEXT NOT NULL,
            pair TEXT NOT NULL, state TEXT NOT NULL, entry_client_order_id TEXT, exit_client_order_id TEXT,
            entry_filled_ts INTEGER, scheduled_exit_ts INTEGER, acquired_qty REAL NOT NULL DEFAULT 0,
            sold_qty REAL NOT NULL DEFAULT 0, locked_exit_qty REAL NOT NULL DEFAULT 0,
            unauthorized_intermediate_order_count INTEGER NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE daily_participation_claims (
            id INTEGER PRIMARY KEY, strategy_instance_id TEXT NOT NULL, pair TEXT NOT NULL, kst_day TEXT NOT NULL,
            participation_policy_hash TEXT NOT NULL, daily_count_snapshot_hash TEXT NOT NULL,
            participation_decision_hash TEXT NOT NULL, fallback_mode TEXT NOT NULL, client_order_id TEXT,
            status TEXT NOT NULL, created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL)""")
        for instance, name, pair in (("daily_participation_sma:old", "daily_participation_sma", "KRW-BTC"), ("h74-observation", "safe_hold", "KRW-BTC"), ("safe_hold:primary", "safe_hold", "KRW-BTC"), ("h74-other", "safe_hold", "KRW-ETH")):
            conn.execute("""INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts)
                VALUES (?, ?, ?, '1h', ?, 'contract', 0, 0, 'ACTIVE', 'HOLD', 1)""", (instance, name, pair, instance))
        if target:
            conn.execute("INSERT INTO target_position_state(pair, target_exposure_krw, target_qty, last_signal, last_reference_price, updated_ts) VALUES ('KRW-BTC', 0, 0, 'HOLD', 0, 1)")
        conn.commit()
    finally:
        conn.close()


def _write_plan(tmp_path: Path, plan: retirement.RetirementPlan) -> Path:
    output = tmp_path / "data" / "paper" / "reports" / "retirement-plan.json"
    retirement.write_plan(output, plan)
    return output


def test_plan_is_deterministic_read_only_and_requires_target_decision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, target=True)
    before = retirement.sha256_file(db)
    required = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action=None)
    retained = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    assert required.status == "operator_decision_required"
    assert retained.status == "ready"
    assert retirement.compute_plan_hash(retained) == retained.plan_hash
    assert retirement.sha256_file(db) == before
    assert not backup.exists()
    assert json.loads(retirement.canonical_json(retained))["status"] == "ready"


def test_apply_is_single_backup_transaction_and_preserves_other_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, target=True)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="clear")
    result = retirement.apply_plan(plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash, confirmation=retirement.CONFIRMATION, broker_local_converged=True)
    assert result["status"] == "applied"
    assert backup.is_file()
    assert retirement.verify_backup(plan=plan, backup_path=backup)["backup_sha256"] == result["backup_sha256"]
    conn = sqlite3.connect(db)
    try:
        assert set(retirement.RETIRED_ORDER_COLUMNS).isdisjoint(retirement._columns(conn, "orders"))
        assert not {"h74_cycle_state", "daily_participation_claims"} & retirement._table_names(conn)
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-BTC'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-ETH'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-BTC'").fetchone()[0] == 0
    finally:
        conn.close()


def test_apply_refuses_stale_plan_and_nonflat_target_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, target=True)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="clear")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE portfolio SET cash_krw=2 WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(retirement.SafetyCheckError, match="retirement_plan_stale"):
        retirement.apply_plan(plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash, confirmation=retirement.CONFIRMATION, broker_local_converged=True)
    second = backup.with_name("nonflat.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE portfolio SET asset_qty=1 WHERE id=1")
    conn.commit()
    conn.close()
    assert retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=second, target_state_action="clear").status == "blocked"


def test_unknown_orders_column_and_existing_backup_block_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE orders ADD COLUMN operator_note TEXT")
    conn.commit()
    conn.close()
    with pytest.raises(retirement.SafetyCheckError, match="unexpected_noncanonical_orders_columns"):
        retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")


def test_apply_requires_reviewed_hash_confirmation_convergence_and_free_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    plan_path = _write_plan(tmp_path, plan)
    with pytest.raises(retirement.SafetyCheckError, match="retirement_plan_hash_mismatch"):
        retirement.apply_plan(plan_path=plan_path, expected_plan_hash="sha256:wrong", confirmation=retirement.CONFIRMATION, broker_local_converged=True)
    with pytest.raises(retirement.SafetyCheckError, match="explicit_confirmation_required"):
        retirement.apply_plan(plan_path=plan_path, expected_plan_hash=plan.plan_hash, confirmation="", broker_local_converged=True)
    with pytest.raises(retirement.SafetyCheckError, match="broker_local_position_convergence"):
        retirement.apply_plan(plan_path=plan_path, expected_plan_hash=plan.plan_hash, confirmation=retirement.CONFIRMATION, broker_local_converged=False)
    _, _, manager = retirement.validate_paths(mode="paper", db_path=db, backup_path=backup)
    with acquire_run_lock(manager.run_lock_path_for_mode("paper")):
        locked = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
        assert locked.status == "blocked"
        with pytest.raises(retirement.SafetyCheckError, match="migration_run_lock_unavailable"):
            retirement.apply_plan(plan_path=plan_path, expected_plan_hash=plan.plan_hash, confirmation=retirement.CONFIRMATION, broker_local_converged=True)


def test_retain_target_state_is_explicit_final_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, target=True)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    result = retirement.apply_plan(plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash, confirmation=retirement.CONFIRMATION, broker_local_converged=True)
    assert result["status"] == "applied_with_retained_target_state"
    assert result["pair_target_state_retained_by_operator_decision"] is True
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-BTC'").fetchone()[0] == 1
    finally:
        conn.close()


def test_cli_outputs_canonical_json_on_refusal(tmp_path: Path) -> None:
    command = [sys.executable, "tools/migrations/retire_removed_strategy.py", "plan", "--mode", "paper", "--pair", "KRW-BTC", "--db", str(tmp_path / "missing.sqlite"), "--backup", str(tmp_path / "backup.sqlite"), "--output", str(tmp_path / "plan.json")]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["status"] == "refused"
