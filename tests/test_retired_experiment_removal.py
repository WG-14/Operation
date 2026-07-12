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

def _migration_module():
    path = Path("tools/migrations/remove_retired_experiment_schema.py")
    spec = importlib.util.spec_from_file_location("retired_schema_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_retired_schema_migration_is_dry_run_safe_and_idempotent(tmp_path: Path) -> None:
    module = _migration_module()
    db_path = (tmp_path / "legacy.sqlite").resolve()
    backup_path = (tmp_path / "backup.sqlite").resolve()
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO orders(client_order_id, status, side, qty_req, qty_filled, created_ts, updated_ts) "
            "VALUES ('filled-order', 'FILLED', 'BUY', 1.0, 1.0, 1, 1)"
        )
        for column in sorted(module.RETIRED_ORDER_COLUMNS):
            conn.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT")
        conn.execute("CREATE TABLE " + "daily_" + "participation_claims(id INTEGER PRIMARY KEY, client_order_id TEXT)")
        conn.commit()
    finally:
        conn.close()

    dry_run = module.run(
        db_path=db_path,
        backup_path=backup_path,
        apply=False,
        confirmation="",
        broker_local_converged=False,
    )
    assert dry_run["retired_order_columns"] == sorted(module.RETIRED_ORDER_COLUMNS)
    assert not backup_path.exists()

    applied = module.run(
        db_path=db_path,
        backup_path=backup_path,
        apply=True,
        confirmation=module.CONFIRMATION,
        broker_local_converged=True,
    )
    assert backup_path.is_file()
    assert applied["backup_sha256"]
    assert applied["foreign_key_check"] == "ok"
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert not module.RETIRED_ORDER_COLUMNS.intersection(columns)
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
    repeated = module.run(
        db_path=db_path,
        backup_path=backup_path,
        apply=True,
        confirmation=module.CONFIRMATION,
        broker_local_converged=True,
    )
    assert repeated["orders"]["rebuilt"] is False
