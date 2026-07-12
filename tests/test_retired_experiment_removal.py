from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from bithumb_bot.db_core import ensure_schema
from bithumb_bot.operation_strategy.registry import (
    OperationStrategyRegistryError,
    resolve_operation_strategy_plugin,
)


H74_RETIRED_VALUES = {
    "h74_entry_plan_client_order_id": "legacy-entry-plan-7",
    "h74_position_ownership_contract_hash": "sha256:legacy-ownership-7",
    "h74_position_ownership_contract": '{"legacy":true,"cycle":"cycle-7"}',
}


def _migration_module():
    path = Path("tools/migrations/remove_retired_experiment_schema.py")
    spec = importlib.util.spec_from_file_location("retired_schema_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _managed_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, mode: str = "paper") -> tuple[Path, Path]:
    roots = {
        "ENV_ROOT": tmp_path / "env",
        "RUN_ROOT": tmp_path / "run",
        "DATA_ROOT": tmp_path / "data",
        "LOG_ROOT": tmp_path / "logs",
        "BACKUP_ROOT": tmp_path / "backup",
        "ARCHIVE_ROOT": tmp_path / "archive",
    }
    monkeypatch.setenv("MODE", mode)
    monkeypatch.delenv("DB_PATH", raising=False)
    for key, root in roots.items():
        monkeypatch.setenv(key, str(root.resolve()))
    return (
        (roots["DATA_ROOT"] / mode / "trades" / f"{mode}.sqlite").resolve(),
        (roots["BACKUP_ROOT"] / mode / "db" / f"{mode}.before-retired-schema.sqlite").resolve(),
    )


def _create_legacy_database(module, db_path: Path, *, status: str = "FILLED") -> dict[str, object]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    values: dict[str, object] = {
        "probe_run_id": "probe-7",
        "client_order_id": "filled-order",
        "submit_attempt_id": "submit-7",
        "exchange_order_id": "exchange-7",
        "status": status,
        "side": "BUY",
        "pair": "KRW-BTC",
        "order_type": "limit",
        "price": 101.25,
        "qty_req": 2.5,
        "qty_filled": 2.5,
        "strategy_name": "safe_hold",
        "strategy_instance_id": "safe_hold:primary",
        "cycle_id": "cycle-7",
        "authority_hash": "authority-7",
        "entry_decision_id": 17,
        "exit_decision_id": 18,
        "decision_reason": "retained decision detail",
        "exit_rule_name": "take_profit",
        "internal_lot_size": 0.1,
        "effective_min_trade_qty": 0.2,
        "qty_step": 0.01,
        "min_notional_krw": 5000.0,
        "intended_lot_count": 25,
        "executable_lot_count": 24,
        "final_intended_qty": 2.5,
        "final_submitted_qty": 2.4,
        "decision_reason_code": "entry_allowed",
        "intent_type": "entry",
        "authority_source": "canonical",
        "entry_authority_source": "canonical",
        "entry_authority_status": "approved",
        "decision_kst_hour": 9,
        "local_intent_state": "intent_recorded",
        "created_ts": 100,
        "updated_ts": 101,
        "last_error": "previous diagnostic",
    }
    try:
        ensure_schema(conn)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO orders ({columns}) VALUES ({placeholders})", tuple(values.values()))
        for column in sorted(module.RETIRED_ORDER_COLUMNS):
            conn.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT")
        for column, value in H74_RETIRED_VALUES.items():
            conn.execute(
                f"UPDATE orders SET {module._quote_identifier(column)}=? "
                "WHERE client_order_id='filled-order'",
                (value,),
            )
        conn.execute(
            "CREATE TABLE h74_position_ownership_state(id INTEGER PRIMARY KEY, contract_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO h74_position_ownership_state(contract_hash) VALUES ('sha256:legacy-ownership-7')"
        )
        conn.execute(
            "CREATE TABLE daily_participation_claims(id INTEGER PRIMARY KEY, client_order_id TEXT)"
        )
        conn.execute("INSERT INTO daily_participation_claims(client_order_id) VALUES ('filled-order')")
        conn.commit()
        return values
    finally:
        conn.close()


def _sqlite_checks(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_retired_strategy_is_not_registered_and_shared_strategies_remain_available() -> None:
    assert resolve_operation_strategy_plugin("sma_with_filter").name == "sma_with_filter"
    assert resolve_operation_strategy_plugin("safe_hold").name == "safe_hold"
    with pytest.raises(OperationStrategyRegistryError, match="unsupported operation strategy"):
        resolve_operation_strategy_plugin("daily_" + "participation_sma")


def test_new_schema_excludes_retired_participation_objects() -> None:
    migration = _migration_module()
    conn = sqlite3.connect(":memory:")
    try:
        ensure_schema(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
    finally:
        conn.close()
    assert "daily_" + "participation_claims" not in tables
    assert not migration.RETIRED_ORDER_COLUMNS.intersection(columns)
    assert {
        "h74_entry_plan_client_order_id",
        "h74_position_ownership_contract_hash",
        "h74_position_ownership_contract",
    }.issubset(migration.RETIRED_ORDER_COLUMNS)
    assert {"orders", "fills", "order_events", "trade_lifecycles"}.issubset(tables)


def test_no_duplicate_top_level_class_or_function_names() -> None:
    for filename in (
        "target_position.py",
        "execution_service.py",
        "run_loop_execution_planner.py",
        "db_core.py",
        "oms.py",
    ):
        tree = ast.parse((Path("src/bithumb_bot") / filename).read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        assert not duplicates, f"{filename}: duplicate top-level definitions {duplicates}"


def test_retired_schema_migration_dry_run_leaves_database_and_backup_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    before_hash = module._sha256(db_path)
    before_mtime = db_path.stat().st_mtime_ns

    report = module.run(
        db_path=db_path,
        backup_path=backup_path,
        mode="paper",
        apply=False,
        confirmation="",
        broker_local_converged=False,
    )

    assert report["status"] == "dry_run"
    assert report["retired_order_columns"] == sorted(module.RETIRED_ORDER_COLUMNS)
    assert report["backup_created"] is False
    assert report["database_modified"] is False
    assert not backup_path.exists()
    assert module._sha256(db_path) == before_hash
    assert db_path.stat().st_mtime_ns == before_mtime
    _sqlite_checks(db_path)


def test_migration_requires_explicit_confirmation_without_creating_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    before = module._sha256(db_path)

    with pytest.raises(module.SafetyCheckError, match="explicit_confirmation_required"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation="wrong",
            broker_local_converged=True,
        )

    assert module._sha256(db_path) == before
    assert not backup_path.exists()


def test_migration_requires_convergence_attestation_without_creating_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)

    with pytest.raises(
        module.SafetyCheckError,
        match="broker_local_position_convergence_operator_attestation_required",
    ):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=False,
        )

    assert not backup_path.exists()


@pytest.mark.parametrize(
    "status",
    ["PENDING_SUBMIT", "NEW", "PARTIAL", "SUBMIT_UNKNOWN", "ACCOUNTING_PENDING", "RECOVERY_REQUIRED", "CANCEL_REQUESTED"],
)
def test_migration_rejects_each_unresolved_order_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path, status=status)

    with pytest.raises(module.SafetyCheckError, match="unresolved_open_order_count=1"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=True,
        )
    assert not backup_path.exists()


def test_migration_refuses_existing_backup_without_changing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(b"do-not-overwrite")
    before_hash = module._sha256(backup_path)
    before_stat = backup_path.stat()

    with pytest.raises(module.SafetyCheckError, match="backup_path_already_exists"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=True,
        )

    after_stat = backup_path.stat()
    assert module._sha256(backup_path) == before_hash
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert backup_path.read_bytes() == b"do-not-overwrite"


def test_first_migration_preserves_all_retained_orders_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    values = _create_legacy_database(module, db_path)

    report = module.run(
        db_path=db_path,
        backup_path=backup_path,
        mode="paper",
        apply=True,
        confirmation=module.CONFIRMATION,
        broker_local_converged=True,
    )

    assert report["status"] == "applied"
    assert report["backup_created"] is True
    assert report["database_modified"] is True
    assert report["backup_sha256"] == module._sha256(backup_path)
    assert report["orders"]["before"] == report["orders"]["after"]
    assert report["orders"]["retained_rows_hash"] == report["orders"]["before"]["retained_columns_hash"]
    assert report["orders"]["before"]["row_count"] == 1
    assert report["orders"]["before"]["max_id"] == 1
    assert report["orders"]["before"]["status_counts"] == {"FILLED": 1}
    assert report["orders"]["before"]["side_counts"] == {"BUY": 1}
    assert report["orders"]["before"]["exchange_order_id_non_null_count"] == 1
    assert report["source_only_columns"] == sorted(module.RETIRED_ORDER_COLUMNS)
    assert report["expected_retired_columns"] == sorted(module.RETIRED_ORDER_COLUMNS)
    assert report["unexpected_source_columns"] == []
    assert report["removed_columns"] == sorted(module.RETIRED_ORDER_COLUMNS)
    assert report["foreign_key_check"] == "ok"
    assert report["integrity_check"] == "ok"
    _sqlite_checks(backup_path)
    _sqlite_checks(db_path)

    backup_conn = sqlite3.connect(backup_path)
    try:
        backup_columns = {row[1] for row in backup_conn.execute("PRAGMA table_info(orders)")}
        assert set(H74_RETIRED_VALUES).issubset(backup_columns)
        backup_h74_values = backup_conn.execute(
            "SELECT " + ", ".join(H74_RETIRED_VALUES) + " FROM orders WHERE client_order_id='filled-order'"
        ).fetchone()
        assert backup_h74_values == tuple(H74_RETIRED_VALUES.values())
    finally:
        backup_conn.close()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert not module.RETIRED_ORDER_COLUMNS.intersection(columns)
        assert "daily_participation_claims" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "h74_position_ownership_state" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        row = conn.execute(
            "SELECT " + ", ".join(values) + " FROM orders WHERE client_order_id='filled-order'"
        ).fetchone()
        assert row == tuple(values.values())
    finally:
        conn.close()


def test_migration_is_true_noop_after_first_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    module.run(
        db_path=db_path,
        backup_path=backup_path,
        mode="paper",
        apply=True,
        confirmation=module.CONFIRMATION,
        broker_local_converged=True,
    )
    backup_hash = module._sha256(backup_path)
    backup_stat = backup_path.stat()
    db_hash = module._sha256(db_path)

    report = module.run(
        db_path=db_path,
        backup_path=backup_path,
        mode="paper",
        apply=True,
        confirmation="",
        broker_local_converged=False,
    )

    assert report["status"] == "already_clean"
    assert report["backup_created"] is False
    assert report["database_modified"] is False
    after_backup_stat = backup_path.stat()
    assert module._sha256(backup_path) == backup_hash
    assert after_backup_stat.st_size == backup_stat.st_size
    assert after_backup_stat.st_mtime_ns == backup_stat.st_mtime_ns
    assert module._sha256(db_path) == db_hash


def test_migration_rolls_back_source_database_after_mid_transaction_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    values = _create_legacy_database(module, db_path)

    def fail_after_copy(stage: str) -> None:
        if stage == "after_orders_copy":
            raise RuntimeError("injected migration failure")

    monkeypatch.setattr(module, "_migration_checkpoint", fail_after_copy)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=True,
        )

    assert backup_path.is_file()
    _sqlite_checks(backup_path)
    _sqlite_checks(db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert "orders" in tables
        assert "daily_participation_claims" in tables
        assert "orders__retired_schema_tmp" not in tables
        assert module.RETIRED_ORDER_COLUMNS.issubset(columns)
        row = conn.execute(
            "SELECT " + ", ".join(values) + " FROM orders WHERE client_order_id='filled-order'"
        ).fetchone()
        assert row == tuple(values.values())
    finally:
        conn.close()


def test_migration_path_policy_rejects_invalid_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    paper_db, paper_backup = _managed_paths(monkeypatch, tmp_path, mode="paper")
    _create_legacy_database(module, paper_db)
    live_db, live_backup = _managed_paths(monkeypatch, tmp_path / "live-case", mode="live")
    _create_legacy_database(module, live_db)
    _managed_paths(monkeypatch, tmp_path, mode="paper")
    paper_cases = [
        (Path("relative.sqlite"), paper_backup, "db_and_backup_paths_must_be_absolute"),
        (paper_db, Path("relative-backup.sqlite"), "db_and_backup_paths_must_be_absolute"),
        (Path.cwd() / "forbidden.sqlite", paper_backup, "database_path_inside_repository"),
        (paper_db, Path.cwd() / "forbidden-backup.sqlite", "backup_path_inside_repository"),
        (paper_db, live_backup, "backup_mode_mismatch"),
        (paper_db, (tmp_path / "outside.sqlite").resolve(), "backup_path_outside_managed_db_bucket"),
    ]
    for db_path, backup_path, error in paper_cases:
        with pytest.raises(module.SafetyCheckError, match=error):
            module.run(
                db_path=db_path,
                backup_path=backup_path,
                mode="paper",
                apply=False,
                confirmation="",
                broker_local_converged=False,
            )
    _managed_paths(monkeypatch, tmp_path / "live-case", mode="live")
    for db_path, backup_path, error in [
        (paper_db, live_backup, "database_mode_mismatch"),
        (live_db, paper_backup, "backup_mode_mismatch"),
    ]:
        with pytest.raises(module.SafetyCheckError, match=error):
            module.run(
                db_path=db_path,
                backup_path=backup_path,
                mode="live",
                apply=False,
                confirmation="",
                broker_local_converged=False,
            )


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_migration_path_policy_accepts_managed_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path, mode=mode)
    _create_legacy_database(module, db_path)
    report = module.run(
        db_path=db_path,
        backup_path=backup_path,
        mode=mode,
        apply=False,
        confirmation="",
        broker_local_converged=False,
    )
    assert report["status"] == "dry_run"


@pytest.mark.parametrize(
    ("unknown_columns", "expected_error"),
    [
        (("operator_note",), "unexpected_noncanonical_orders_columns:operator_note"),
        (
            ("future_execution_metadata", "operator_note"),
            "unexpected_noncanonical_orders_columns:future_execution_metadata,operator_note",
        ),
    ],
)
def test_migration_refuses_unknown_source_only_orders_columns_before_backup_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unknown_columns: tuple[str, ...],
    expected_error: str,
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    conn = sqlite3.connect(db_path)
    try:
        for column in unknown_columns:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {module._quote_identifier(column)} TEXT")
            conn.execute(
                f"UPDATE orders SET {module._quote_identifier(column)}=? "
                "WHERE client_order_id='filled-order'",
                ("must-not-be-silently-dropped",),
            )
        conn.commit()
    finally:
        conn.close()
    before_hash = module._sha256(db_path)

    with pytest.raises(module.SafetyCheckError, match=expected_error):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=True,
        )

    assert not backup_path.exists()
    assert module._sha256(db_path) == before_hash
    _sqlite_checks(db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert set(unknown_columns).issubset(columns)
        assert module.RETIRED_ORDER_COLUMNS.issubset(columns)
        assert "daily_participation_claims" in tables
        assert "h74_position_ownership_state" in tables
        assert "orders__retired_schema_tmp" not in tables
        for column in unknown_columns:
            assert conn.execute(
                f"SELECT {module._quote_identifier(column)} FROM orders WHERE client_order_id='filled-order'"
            ).fetchone() == ("must-not-be-silently-dropped",)
    finally:
        conn.close()


def test_migration_refuses_unknown_orders_schema_objects_before_backup_or_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    _create_legacy_database(module, db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE UNIQUE INDEX operator_orders_client_unique ON orders(client_order_id)")
        conn.execute(
            "CREATE INDEX operator_orders_filled_partial ON orders(status) WHERE status='FILLED'"
        )
        conn.execute(
            "CREATE TRIGGER operator_orders_update_trigger BEFORE UPDATE ON orders BEGIN SELECT 1; END"
        )
        conn.commit()
    finally:
        conn.close()
    before_hash = module._sha256(db_path)

    with pytest.raises(
        module.SafetyCheckError,
        match=(
            "unexpected_orders_schema_objects:operator_orders_client_unique,"
            "operator_orders_filled_partial,operator_orders_update_trigger"
        ),
    ):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=True,
            confirmation=module.CONFIRMATION,
            broker_local_converged=True,
        )

    assert not backup_path.exists()
    assert module._sha256(db_path) == before_hash
    _sqlite_checks(db_path)
    conn = sqlite3.connect(db_path)
    try:
        objects = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name='orders' AND type IN ('index', 'trigger')"
            )
        }
        assert {
            "operator_orders_client_unique",
            "operator_orders_filled_partial",
            "operator_orders_update_trigger",
        }.issubset(objects)
        assert "orders__retired_schema_tmp" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


@pytest.mark.parametrize("relationship", ["same", "backup_under_data", "data_under_backup"])
def test_migration_live_rejects_overlapping_managed_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relationship: str
) -> None:
    module = _migration_module()
    base = (tmp_path / relationship).resolve()
    data_root = base / "data"
    backup_root = base / "backup"
    if relationship == "same":
        backup_root = data_root
    elif relationship == "backup_under_data":
        backup_root = data_root / "backup"
    else:
        data_root = backup_root / "data"
    roots = {
        "ENV_ROOT": base / "env",
        "RUN_ROOT": base / "run",
        "DATA_ROOT": data_root,
        "LOG_ROOT": base / "logs",
        "BACKUP_ROOT": backup_root,
        "ARCHIVE_ROOT": base / "archive",
    }
    monkeypatch.setenv("MODE", "live")
    monkeypatch.delenv("DB_PATH", raising=False)
    for key, root in roots.items():
        monkeypatch.setenv(key, str(root))
    db_path = (data_root / "live" / "trades" / "live.sqlite").resolve()
    backup_path = (backup_root / "live" / "db" / "live.before-retired-schema.sqlite").resolve()
    _create_legacy_database(module, db_path)

    with pytest.raises(module.SafetyCheckError, match="path_manager_policy_error:runtime roots must not overlap"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="live",
            apply=False,
            confirmation="",
            broker_local_converged=False,
        )


def test_migration_rejects_relative_db_path_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path, mode="live")
    _create_legacy_database(module, db_path)
    monkeypatch.setenv("DB_PATH", "relative/live.sqlite")

    with pytest.raises(module.SafetyCheckError, match="configured_db_path_must_be_absolute"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="live",
            apply=False,
            confirmation="",
            broker_local_converged=False,
        )


def test_migration_rejects_db_path_override_outside_managed_trades_bucket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    _, backup_path = _managed_paths(monkeypatch, tmp_path)
    outside_db = (tmp_path / "outside" / "paper.sqlite").resolve()
    _create_legacy_database(module, outside_db)
    monkeypatch.setenv("DB_PATH", str(outside_db))

    with pytest.raises(module.SafetyCheckError, match="database_mode_mismatch"):
        module.run(
            db_path=outside_db,
            backup_path=backup_path,
            mode="paper",
            apply=False,
            confirmation="",
            broker_local_converged=False,
        )


def test_migration_rejects_db_path_override_that_differs_from_db_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _migration_module()
    db_path, backup_path = _managed_paths(monkeypatch, tmp_path)
    configured_db = db_path.with_name("configured-paper.sqlite")
    _create_legacy_database(module, db_path)
    _create_legacy_database(module, configured_db)
    monkeypatch.setenv("DB_PATH", str(configured_db))

    with pytest.raises(module.SafetyCheckError, match="database_mode_mismatch"):
        module.run(
            db_path=db_path,
            backup_path=backup_path,
            mode="paper",
            apply=False,
            confirmation="",
            broker_local_converged=False,
        )
