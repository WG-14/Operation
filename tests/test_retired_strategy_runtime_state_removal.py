from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

import pytest

from bithumb_bot.db_core import ensure_schema
from bithumb_bot.run_lock import acquire_run_lock


def _module():
    path = Path("tools/migrations/remove_retired_strategy_runtime_state.py")
    spec = importlib.util.spec_from_file_location("retired_runtime_state_cleanup", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    roots = {key: tmp_path / value for key, value in {
        "ENV_ROOT": "env", "RUN_ROOT": "run", "DATA_ROOT": "data", "LOG_ROOT": "logs",
        "BACKUP_ROOT": "backup", "ARCHIVE_ROOT": "archive",
    }.items()}
    monkeypatch.setenv("MODE", "paper")
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value.resolve()))
    return ((roots["DATA_ROOT"] / "paper" / "trades" / "paper.sqlite").resolve(), (roots["BACKUP_ROOT"] / "paper" / "db" / "cleanup.sqlite").resolve())


def _create_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        ensure_schema(conn)
        conn.execute("INSERT INTO portfolio(id, cash_krw, asset_qty) VALUES (1, 1, 0)")
        conn.execute(
            """INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts
            ) VALUES ('daily_participation_sma:legacy', 'daily_participation_sma', 'KRW-BTC', '1h', 'scope-a', 'contract-a', 0, 0, 'ACTIVE', 'BUY', 1)"""
        )
        conn.execute(
            """INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts
            ) VALUES ('h74-source-observation', 'safe_hold', 'KRW-BTC', '1h', 'scope-b', 'contract-b', 0, 0, 'ACTIVE', 'HOLD', 1)"""
        )
        conn.execute(
            """INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts
            ) VALUES ('safe_hold:primary', 'safe_hold', 'KRW-BTC', '1h', 'scope-c', 'contract-c', 0, 0, 'ACTIVE', 'HOLD', 1)"""
        )
        conn.execute(
            """INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts
            ) VALUES ('h74-other', 'safe_hold', 'KRW-ETH', '1h', 'scope-d', 'contract-d', 0, 0, 'ACTIVE', 'HOLD', 1)"""
        )
        conn.execute("INSERT INTO target_position_state(pair, target_exposure_krw, target_qty, last_signal, last_reference_price, updated_ts) VALUES ('KRW-BTC', 0, 0, 'HOLD', 0, 1)")
        conn.commit()
    finally:
        conn.close()


def test_dry_run_detects_only_retired_rows_without_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    db, backup = _paths(monkeypatch, tmp_path)
    _create_db(db)
    before = module._sha256(db)
    report = module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=False, confirmation="", broker_local_converged=False, clear_pair_target_state=False)
    assert report["status"] == "dry_run"
    assert report["retired_virtual_target_state_count"] == 2
    assert report["pair_target_position_state_present"] is True
    assert report["run_lock_acquired"] is False
    assert module._sha256(db) == before
    assert not backup.exists()


def test_apply_removes_retired_rows_and_explicit_pair_target_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    db, backup = _paths(monkeypatch, tmp_path)
    _create_db(db)
    report = module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=True, confirmation=module.CONFIRMATION, broker_local_converged=True, clear_pair_target_state=True)
    assert report["status"] == "applied"
    assert report["pair_target_position_state_removed"] is True
    assert backup.is_file()
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-BTC'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-ETH'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-BTC'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    finally:
        conn.close()


def test_pair_target_requires_explicit_clear_and_flat_portfolio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    db, backup = _paths(monkeypatch, tmp_path)
    _create_db(db)
    module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=True, confirmation=module.CONFIRMATION, broker_local_converged=True, clear_pair_target_state=False)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-BTC'").fetchone()[0] == 1
        conn.execute("UPDATE portfolio SET asset_qty=0.1 WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(module.SafetyCheckError, match="pair_target_state_requires_flat_portfolio"):
        module.run(db_path=db, backup_path=backup.with_name("flat.sqlite"), mode="paper", pair="KRW-BTC", apply=True, confirmation=module.CONFIRMATION, broker_local_converged=True, clear_pair_target_state=True)


def test_apply_requires_confirmation_attestation_and_available_run_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    db, backup = _paths(monkeypatch, tmp_path)
    _create_db(db)
    with pytest.raises(module.SafetyCheckError, match="explicit_confirmation_required"):
        module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=True, confirmation="", broker_local_converged=True, clear_pair_target_state=False)
    with pytest.raises(module.SafetyCheckError, match="broker_local_position_convergence_operator_attestation_required"):
        module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=True, confirmation=module.CONFIRMATION, broker_local_converged=False, clear_pair_target_state=False)
    manager = module._validate_paths(db_path=db, backup_path=backup, mode="paper")[2]
    with acquire_run_lock(manager.run_lock_path_for_mode("paper")):
        with pytest.raises(module.SafetyCheckError, match="migration_run_lock_unavailable"):
            module.run(db_path=db, backup_path=backup, mode="paper", pair="KRW-BTC", apply=True, confirmation=module.CONFIRMATION, broker_local_converged=True, clear_pair_target_state=False)
