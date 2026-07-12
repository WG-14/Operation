from __future__ import annotations

import json
import os
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


def _legacy_db(
    db_path: Path, *, target: bool = False, asset_qty: float = 0.0, with_child_history: bool = False,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
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
        if with_child_history:
            conn.execute("""CREATE TABLE extra_order_evidence (
                id INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL, evidence TEXT NOT NULL,
                FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id))""")
        for instance, name, pair in (("daily_participation_sma:old", "daily_participation_sma", "KRW-BTC"), ("h74-observation", "safe_hold", "KRW-BTC"), ("safe_hold:primary", "safe_hold", "KRW-BTC"), ("h74-other", "safe_hold", "KRW-ETH")):
            conn.execute("""INSERT INTO strategy_virtual_target_state(
                strategy_instance_id, strategy_name, pair, interval, scope_key_hash, runtime_contract_hash,
                virtual_target_exposure_krw, virtual_target_qty, lifecycle_state, last_signal, updated_ts)
                VALUES (?, ?, ?, '1h', ?, 'contract', 0, 0, 'ACTIVE', 'HOLD', 1)""", (instance, name, pair, instance))
        if with_child_history:
            conn.execute("""INSERT INTO orders(
                client_order_id, status, side, pair, qty_req, qty_filled, created_ts, updated_ts
            ) VALUES ('legacy-filled-order', 'FILLED', 'BUY', 'KRW-BTC', 0.001, 0.001, 1, 2)""")
            conn.execute(
                "UPDATE orders SET " + ", ".join(
                    f"{retirement._quote(column)}=?" for column in sorted(retirement.RETIRED_ORDER_COLUMNS)
                ) + " WHERE client_order_id='legacy-filled-order'",
                tuple(f"retired-{index}" for index, _ in enumerate(sorted(retirement.RETIRED_ORDER_COLUMNS), start=1)),
            )
            conn.execute("""INSERT INTO fills(
                client_order_id, fill_id, fill_ts, price, qty
            ) VALUES ('legacy-filled-order', 'legacy-fill-1', 3, 100000000, 0.001)""")
            conn.execute("""INSERT INTO order_events(
                client_order_id, event_type, event_ts, order_status
            ) VALUES ('legacy-filled-order', 'filled', 4, 'FILLED')""")
            conn.execute("INSERT INTO extra_order_evidence(client_order_id, evidence) VALUES ('legacy-filled-order', 'retained')")
        if target:
            conn.execute("INSERT INTO target_position_state(pair, target_exposure_krw, target_qty, last_signal, last_reference_price, updated_ts) VALUES ('KRW-BTC', 0, 0, 'HOLD', 0, 1)")
        conn.commit()
    finally:
        conn.close()


def _write_plan(tmp_path: Path, plan: retirement.RetirementPlan) -> Path:
    output = tmp_path / "data" / "paper" / "reports" / "retirement-plan.json"
    retirement.write_plan(output, plan)
    return output


def _fk_history_inventory(conn: sqlite3.Connection) -> dict[str, object]:
    orders = retirement._orders_contract(conn)
    protected = retirement._protected_inventory(conn, list(orders["retained_columns"]))
    dependents = [dict(item) for item in retirement.orders_foreign_key_inventory(conn)]
    return {
        "orders_dependents": dependents,
        "children": {str(item["child_table"]): protected[str(item["child_table"])] for item in dependents},
    }


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
    assert result["backup_verified"] is True
    assert result["foreign_keys_reenabled"] is True
    assert result["post_commit_verified"] is True
    assert retirement.verify_backup(plan=plan, backup_path=backup, expected_sha256=result["backup_sha256"])["backup_sha256"] == result["backup_sha256"]
    conn = sqlite3.connect(db)
    try:
        assert set(retirement.RETIRED_ORDER_COLUMNS).isdisjoint(retirement._columns(conn, "orders"))
        assert not {"h74_cycle_state", "daily_participation_claims"} & retirement._table_names(conn)
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-BTC'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM strategy_virtual_target_state WHERE pair='KRW-ETH'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM target_position_state WHERE pair='KRW-BTC'").fetchone()[0] == 0
    finally:
        conn.close()


def test_apply_rebuilds_orders_with_foreign_key_child_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, with_child_history=True)
    before = sqlite3.connect(db)
    try:
        before.execute("PRAGMA foreign_keys=ON")
        assert before.execute("PRAGMA foreign_keys").fetchone() == (1,)
        history_before = _fk_history_inventory(before)
        max_order_id = before.execute("SELECT MAX(id) FROM orders").fetchone()[0]
        expected_child_fk = {
            "foreign_key_id": 0, "sequence": 0, "from_column": "client_order_id",
            "parent_table": "orders", "to_column": "client_order_id",
            "on_update": "NO ACTION", "on_delete": "NO ACTION", "match": "NONE",
        }
        dependents_by_child = {
            item["child_table"]: {key: value for key, value in item.items() if key != "child_table"}
            for item in history_before["orders_dependents"]
        }
        assert dependents_by_child["fills"] == expected_child_fk
        assert dependents_by_child["order_events"] == expected_child_fk
        assert dependents_by_child["extra_order_evidence"] == expected_child_fk
    finally:
        before.close()
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    assert plan.status == "ready"
    assert plan.schema_inventory["orders_foreign_key_dependents"] == history_before["orders_dependents"]
    assert plan.protected_inventory["extra_order_evidence"] == history_before["children"]["extra_order_evidence"]
    result = retirement.apply_plan(
        plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
        confirmation=retirement.CONFIRMATION, broker_local_converged=True,
    )
    assert result["status"] == "applied"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert set(retirement.RETIRED_ORDER_COLUMNS).isdisjoint(retirement._columns(conn, "orders"))
        assert conn.execute("SELECT status, side, qty_req, qty_filled FROM orders WHERE client_order_id='legacy-filled-order'").fetchone() == ("FILLED", "BUY", 0.001, 0.001)
        assert _fk_history_inventory(conn) == history_before
        conn.execute("""INSERT INTO orders(
            client_order_id, status, side, pair, qty_req, qty_filled, created_ts, updated_ts
        ) VALUES ('post-retirement-order', 'FILLED', 'BUY', 'KRW-BTC', 0.001, 0.001, 5, 6)""")
        new_order_id = conn.execute("SELECT id FROM orders WHERE client_order_id='post-retirement-order'").fetchone()[0]
        assert new_order_id > max_order_id
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='orders'").fetchone()[0] >= new_order_id
        conn.execute("INSERT INTO fills(client_order_id, fill_id, fill_ts, price, qty) VALUES ('post-retirement-order', 'post-retirement-fill', 7, 100000000, 0.001)")
        conn.execute("INSERT INTO order_events(client_order_id, event_type, event_ts, order_status) VALUES ('post-retirement-order', 'filled', 8, 'FILLED')")
        conn.commit()
    finally:
        conn.close()
    backup_conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        assert _fk_history_inventory(backup_conn) == history_before
        assert set(retirement.RETIRED_ORDER_COLUMNS).issubset(retirement._columns(backup_conn, "orders"))
    finally:
        backup_conn.close()


def test_apply_rolls_back_orders_rebuild_and_reenables_foreign_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db, with_child_history=True)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    original_connect = sqlite3.connect
    pragma_statements: list[str] = []

    class AuditedConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
            if sql.upper().startswith("PRAGMA FOREIGN_KEYS"):
                pragma_statements.append(sql.upper())
            return self._connection.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def audited_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection | AuditedConnection:
        connection = original_connect(database, *args, **kwargs)
        return AuditedConnection(connection) if database == db else connection

    def fail_after_rebuild(stage: str) -> None:
        if stage == "after_orders_rebuilt":
            raise retirement.SafetyCheckError("injected_rebuild_failure")

    monkeypatch.setattr(retirement.sqlite3, "connect", audited_connect)
    monkeypatch.setattr(retirement, "_apply_checkpoint", fail_after_rebuild)
    with pytest.raises(retirement.RetirementApplyError, match="injected_rebuild_failure") as failure:
        retirement.apply_plan(
            plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
            confirmation=retirement.CONFIRMATION, broker_local_converged=True,
        )
    payload = failure.value.as_payload()
    assert payload["status"] == "apply_failed_rolled_back"
    assert payload["backup_created"] is True
    assert payload["backup_verified"] is True
    assert payload["rollback_succeeded"] is True
    assert payload["database_modified"] is False
    assert payload["transaction_committed"] is False
    assert payload["foreign_keys_reenabled"] is True
    assert "PRAGMA FOREIGN_KEYS=OFF" in pragma_statements
    assert pragma_statements[-2:] == ["PRAGMA FOREIGN_KEYS=ON", "PRAGMA FOREIGN_KEYS"]
    conn = original_connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        assert set(retirement.RETIRED_ORDER_COLUMNS).issubset(retirement._columns(conn, "orders"))
        assert conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id='legacy-filled-order'").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM order_events WHERE client_order_id='legacy-filled-order'").fetchone() == (1,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "orders__removed_strategy_retirement_tmp" not in retirement._table_names(conn)
    finally:
        conn.close()
    assert retirement.verify_backup(plan=plan, backup_path=backup, expected_sha256=retirement.sha256_file(backup))["status"] == "backup_verified"


def test_apply_reports_created_backup_when_checkpoint_fails_before_transaction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")

    def fail(stage: str) -> None:
        if stage == "after_backup_created":
            raise retirement.SafetyCheckError("injected_backup_failure")

    monkeypatch.setattr(retirement, "_apply_checkpoint", fail)
    with pytest.raises(retirement.RetirementApplyError) as failure:
        retirement.apply_plan(
            plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
            confirmation=retirement.CONFIRMATION, broker_local_converged=True,
        )
    payload = failure.value.as_payload()
    assert payload["status"] == "apply_failed_before_transaction"
    assert payload["backup_created"] is True
    assert payload["backup_verified"] is False
    assert backup.is_file()
    assert payload["database_modified"] is False
    assert payload["transaction_committed"] is False
    assert failure.value.exit_code == 3


def test_apply_promotes_failed_rollback_verification_to_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")

    def fail(stage: str) -> None:
        if stage == "after_orders_rebuilt":
            raise retirement.SafetyCheckError("injected_transaction_failure")

    def fail_rollback_verification(*_args: object) -> None:
        raise retirement.SafetyCheckError("injected_rollback_verification_failure")

    monkeypatch.setattr(retirement, "_apply_checkpoint", fail)
    monkeypatch.setattr(retirement, "_verify_rollback_state", fail_rollback_verification)
    with pytest.raises(retirement.RetirementApplyError) as failure:
        retirement.apply_plan(
            plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
            confirmation=retirement.CONFIRMATION, broker_local_converged=True,
        )
    payload = failure.value.as_payload()
    assert payload["status"] == "apply_outcome_unknown"
    assert payload["database_modified"] is None
    assert payload["rollback_succeeded"] is False
    assert payload["recovery_required"] is True
    assert failure.value.exit_code == 4


def test_apply_treats_commit_exception_as_unknown_outcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    original_connect = sqlite3.connect

    class CommitFailConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def commit(self) -> None:
            raise sqlite3.OperationalError("injected_commit_failure")

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def fail_commit_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection | CommitFailConnection:
        connection = original_connect(database, *args, **kwargs)
        return CommitFailConnection(connection) if Path(database) == db else connection

    monkeypatch.setattr(retirement.sqlite3, "connect", fail_commit_connect)
    with pytest.raises(retirement.RetirementApplyError) as failure:
        retirement.apply_plan(
            plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
            confirmation=retirement.CONFIRMATION, broker_local_converged=True,
        )
    payload = failure.value.as_payload()
    assert payload["status"] == "apply_commit_outcome_unknown"
    assert payload["database_modified"] is None
    assert payload["commit_outcome"] == "unknown"
    assert payload["backup_created"] is True
    assert payload["recovery_required"] is True
    assert failure.value.exit_code == 4


def test_apply_reports_committed_database_when_post_commit_verification_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")

    def fail(stage: str) -> None:
        if stage == "after_commit":
            raise retirement.SafetyCheckError("injected_post_commit_failure")

    monkeypatch.setattr(retirement, "_apply_checkpoint", fail)
    with pytest.raises(retirement.RetirementApplyError) as failure:
        retirement.apply_plan(
            plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
            confirmation=retirement.CONFIRMATION, broker_local_converged=True,
        )
    payload = failure.value.as_payload()
    assert payload["status"] == "applied_verification_failed"
    assert payload["database_modified"] is True
    assert payload["transaction_committed"] is True
    assert payload["commit_outcome"] == "committed"
    assert payload["backup_created"] is True
    assert payload["backup_verified"] is True
    assert payload["recovery_required"] is True
    assert failure.value.exit_code == 4
    conn = sqlite3.connect(db)
    try:
        assert set(retirement.RETIRED_ORDER_COLUMNS).isdisjoint(retirement._columns(conn, "orders"))
        assert not {"h74_cycle_state", "daily_participation_claims"} & retirement._table_names(conn)
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


@pytest.mark.parametrize(
    ("injection", "status", "exit_code"),
    [
        ("after_backup_created", "apply_failed_before_transaction", 3),
        ("after_orders_rebuilt", "apply_failed_rolled_back", 3),
        ("rollback_verification", "apply_outcome_unknown", 4),
        ("commit_failure", "apply_commit_outcome_unknown", 4),
        ("after_commit", "applied_verification_failed", 4),
    ],
)
def test_cli_apply_failure_outputs_canonical_recovery_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, injection: str, status: str, exit_code: int,
) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    plan_path = _write_plan(tmp_path, plan)
    injection_dir = tmp_path / "injection"
    injection_dir.mkdir()
    source_dir = Path("tools/migrations").resolve()
    (injection_dir / "sitecustomize.py").write_text(
        f"""import sys
from pathlib import Path
sys.path.insert(0, {str(source_dir)!r})
import _offline_retirement as retirement

_original_connect = retirement.sqlite3.connect

if {injection!r} == 'rollback_verification':
    def _failed_rollback_verification(*_args):
        raise retirement.SafetyCheckError('injected_rollback_verification_failure')
    retirement._verify_rollback_state = _failed_rollback_verification

if {injection!r} == 'commit_failure':
    class _CommitFailConnection:
        def __init__(self, connection):
            self._connection = connection
        @property
        def in_transaction(self):
            return self._connection.in_transaction
        def commit(self):
            raise retirement.sqlite3.OperationalError('injected_commit_failure')
        def __getattr__(self, name):
            return getattr(self._connection, name)
    def _fail_commit_connect(database, *args, **kwargs):
        connection = _original_connect(database, *args, **kwargs)
        return _CommitFailConnection(connection) if Path(database) == Path({str(db)!r}) else connection
    retirement.sqlite3.connect = _fail_commit_connect

def _checkpoint(stage):
    if stage == {injection!r} or ({injection!r} == 'rollback_verification' and stage == 'after_orders_rebuilt'):
        raise retirement.SafetyCheckError('injected_' + stage + '_failure')
retirement._apply_checkpoint = _checkpoint
""",
        encoding="utf-8",
    )
    environment = {**os.environ, "PYTHONPATH": str(injection_dir)}
    completed = subprocess.run(
        [
            sys.executable, "tools/migrations/retire_removed_strategy.py", "apply",
            "--plan", str(plan_path), "--plan-hash", plan.plan_hash,
            "--broker-local-converged", "--confirm", retirement.CONFIRMATION,
        ],
        text=True, capture_output=True, check=False, env=environment,
    )
    assert completed.returncode == exit_code
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert completed.stdout == retirement.canonical_json(payload) + "\n"
    for field in (
        "status", "reason_code", "phase", "database_modified", "backup_created",
        "transaction_committed", "commit_outcome", "recovery_required", "recommended_action",
    ):
        assert field in payload
    assert payload["status"] == status


def test_verify_backup_requires_recorded_expected_sha256(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db, backup = _paths(monkeypatch, tmp_path)
    _legacy_db(db)
    plan = retirement.build_plan(mode="paper", pair="KRW-BTC", db_path=db, backup_path=backup, target_state_action="retain")
    result = retirement.apply_plan(
        plan_path=_write_plan(tmp_path, plan), expected_plan_hash=plan.plan_hash,
        confirmation=retirement.CONFIRMATION, broker_local_converged=True,
    )
    with pytest.raises(retirement.SafetyCheckError, match="backup_expected_sha256_required"):
        retirement.verify_backup(plan=plan, backup_path=backup, expected_sha256="")
    with pytest.raises(retirement.SafetyCheckError, match="backup_sha256_mismatch"):
        retirement.verify_backup(plan=plan, backup_path=backup, expected_sha256="0" * 64)
    assert retirement.verify_backup(plan=plan, backup_path=backup, expected_sha256=result["backup_sha256"])["status"] == "backup_verified"
    command = [
        sys.executable, "tools/migrations/retire_removed_strategy.py", "verify-backup",
        "--plan", str(_write_plan(tmp_path, plan)), "--backup", str(backup),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason_code"] == "invalid_arguments"
