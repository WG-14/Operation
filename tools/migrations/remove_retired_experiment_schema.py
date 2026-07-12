#!/usr/bin/env python3
"""Retire legacy experiment schema only after an operator establishes a safe stop point.

This command is intentionally not part of startup migrations. References to
retired names in this file are legacy-schema migration only; not an active
runtime feature.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections import namedtuple
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
from typing import Iterable, Iterator

from bithumb_bot.paths import (
    ALLOWED_MODES,
    PathManager,
    PathPolicyError,
    validate_runtime_root_separation,
)
from bithumb_bot.run_lock import RunLockError, acquire_run_lock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION = "REMOVE_RETIRED_EXPERIMENT_SCHEMA"
# legacy retired-schema migration only; not an active runtime feature
RETIRED_ORDER_COLUMNS = frozenset(
    {
        "h74_entry_plan_client_order_id",
        "h74_position_ownership_contract_hash",
        "h74_position_ownership_contract",
        "daily_participation_policy_hash",
        "daily_count_snapshot_hash",
        "participation_decision_hash",
        "daily_participation_kst_day",
        "daily_participation_fallback_mode",
    }
)
# These are the only retired tables verified against the removed implementations.
# A familiar prefix is never enough authority to delete a table.
RETIRED_TABLES = frozenset({"h74_cycle_state", "daily_participation_claims"})
RETIRED_TABLE_CONTRACTS = {
    "h74_cycle_state": {
        "required_columns": frozenset(
            {
                "cycle_id",
                "authority_hash",
                "strategy_instance_id",
                "pair",
                "state",
                "entry_client_order_id",
                "exit_client_order_id",
                "entry_filled_ts",
                "scheduled_exit_ts",
                "acquired_qty",
                "sold_qty",
                "locked_exit_qty",
                "unauthorized_intermediate_order_count",
                "updated_ts",
            }
        ),
        "allowed_columns": frozenset(
            {
                "cycle_id",
                "authority_hash",
                "strategy_instance_id",
                "pair",
                "state",
                "entry_client_order_id",
                "exit_client_order_id",
                "entry_filled_ts",
                "scheduled_exit_ts",
                "acquired_qty",
                "sold_qty",
                "locked_exit_qty",
                "unauthorized_intermediate_order_count",
                "updated_ts",
                "contract_hash",
                "h74_entry_plan_client_order_id",
            }
        ),
    },
    "daily_participation_claims": {
        "required_columns": frozenset(
            {
                "id",
                "strategy_instance_id",
                "pair",
                "kst_day",
                "participation_policy_hash",
                "daily_count_snapshot_hash",
                "participation_decision_hash",
                "fallback_mode",
                "client_order_id",
                "status",
                "created_ts",
                "updated_ts",
            }
        ),
        "allowed_columns": frozenset(
            {
                "id",
                "strategy_instance_id",
                "pair",
                "kst_day",
                "participation_policy_hash",
                "daily_count_snapshot_hash",
                "participation_decision_hash",
                "fallback_mode",
                "client_order_id",
                "status",
                "retry_allowed",
                "created_ts",
                "updated_ts",
            }
        ),
    },
}
UNSAFE_ORDER_STATUSES = frozenset(
    {
        "PENDING_SUBMIT",
        "NEW",
        "PARTIAL",
        "SUBMIT_UNKNOWN",
        "ACCOUNTING_PENDING",
        "RECOVERY_REQUIRED",
        "CANCEL_REQUESTED",
    }
)


class SafetyCheckError(RuntimeError):
    pass


SchemaObject = namedtuple("SchemaObject", ("object_type", "name", "sql"))


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _holders(db_path: Path) -> list[int]:
    """Return Linux processes holding the database; unavailable /proc fails closed."""
    if os.name != "posix" or not Path("/proc").is_dir():
        raise SafetyCheckError("active_process_check_unavailable")
    resolved = db_path.resolve()
    holders: list[int] = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    if fd.resolve() == resolved and int(pid_dir.name) != os.getpid():
                        holders.append(int(pid_dir.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return holders


def _scalar(conn: sqlite3.Connection, query: str, params: Iterable[object] = ()) -> int:
    row = conn.execute(query, tuple(params)).fetchone()
    return int(row[0] if row else 0)


def _require_safe_stop(conn: sqlite3.Connection, db_path: Path, broker_local_converged: bool) -> None:
    if _holders(db_path):
        raise SafetyCheckError("database_has_active_process_holder")
    tables = _table_names(conn)
    if "orders" not in tables:
        raise SafetyCheckError("orders_table_missing")
    statuses = sorted(UNSAFE_ORDER_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    unsafe = _scalar(conn, f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})", statuses)
    if unsafe:
        raise SafetyCheckError(f"unresolved_open_order_count={unsafe}")
    if not broker_local_converged:
        raise SafetyCheckError("broker_local_position_convergence_operator_attestation_required")


def _canonical_orders_sql() -> tuple[str, list[SchemaObject]]:
    """Obtain the current canonical schema from db_core without touching disk."""
    from bithumb_bot.db_core import ensure_schema

    canonical = sqlite3.connect(":memory:")
    try:
        ensure_schema(canonical)
        row = canonical.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        if row is None or not row[0]:
            raise SafetyCheckError("canonical_orders_schema_missing")
        return str(row[0]), _orders_schema_objects(canonical)
    finally:
        canonical.close()


def _canonical_orders_columns() -> list[str]:
    sql, _ = _canonical_orders_sql()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(sql)
        return _table_columns(conn, "orders")
    finally:
        conn.close()


def _orders_schema_objects(conn: sqlite3.Connection) -> list[SchemaObject]:
    """Return explicitly defined indexes/triggers attached to ``orders``."""
    return [
        SchemaObject(object_type=str(row[0]), name=str(row[1]), sql=str(row[2]))
        for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name='orders' AND type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        )
    ]


def _orders_auto_indexes(conn: sqlite3.Connection) -> list[tuple[str, tuple[object, ...]]]:
    """Inventory SQLite-managed indexes separately from explicit schema objects."""
    auto_indexes: list[tuple[str, tuple[object, ...]]] = []
    for row in conn.execute("PRAGMA index_list(orders)"):
        name = str(row[1])
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if sql_row is None or sql_row[0] is not None:
            continue
        columns = tuple(str(column[2]) for column in conn.execute(f"PRAGMA index_info({_quote_identifier(name)})"))
        unique = int(row[2]) if len(row) > 2 else 0
        origin = str(row[3]) if len(row) > 3 else ""
        partial = int(row[4]) if len(row) > 4 else 0
        auto_indexes.append((name, (unique, origin, partial, columns)))
    return sorted(auto_indexes)


def _canonical_orders_schema_inventory() -> tuple[list[SchemaObject], list[tuple[str, tuple[object, ...]]]]:
    """Read canonical orders objects from an in-memory current schema."""
    from bithumb_bot.db_core import ensure_schema

    canonical = sqlite3.connect(":memory:")
    try:
        ensure_schema(canonical)
        return _orders_schema_objects(canonical), _orders_auto_indexes(canonical)
    finally:
        canonical.close()


def _unexpected_orders_schema_object_names(conn: sqlite3.Connection) -> list[str]:
    """Fail closed when a rebuild would drop non-canonical orders objects."""
    source_objects = _orders_schema_objects(conn)
    canonical_objects, canonical_auto_indexes = _canonical_orders_schema_inventory()
    canonical_object_keys = {(item.object_type, item.name, item.sql) for item in canonical_objects}
    unexpected_names = {
        item.name
        for item in source_objects
        if (item.object_type, item.name, item.sql) not in canonical_object_keys
    }

    remaining_canonical_auto = [signature for _, signature in canonical_auto_indexes]
    for name, signature in _orders_auto_indexes(conn):
        if signature in remaining_canonical_auto:
            remaining_canonical_auto.remove(signature)
        else:
            unexpected_names.add(name)
    return sorted(unexpected_names)


def _orders_rebuild_contract(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Classify every source-only orders column before any mutation or backup."""
    source_columns = set(_table_columns(conn, "orders"))
    target_columns = set(_canonical_orders_columns())
    source_only_columns = source_columns - target_columns
    expected_retired_columns = source_only_columns & RETIRED_ORDER_COLUMNS
    unexpected_source_columns = source_only_columns - RETIRED_ORDER_COLUMNS
    return {
        "source_only_columns": sorted(source_only_columns),
        "expected_retired_columns": sorted(expected_retired_columns),
        "unexpected_source_columns": sorted(unexpected_source_columns),
    }


def _require_orders_rebuild_contract(conn: sqlite3.Connection) -> dict[str, list[str]]:
    contract = _orders_rebuild_contract(conn)
    unexpected_source_columns = contract["unexpected_source_columns"]
    if unexpected_source_columns:
        raise SafetyCheckError(
            "unexpected_noncanonical_orders_columns:" + ",".join(unexpected_source_columns)
        )
    unexpected_objects = _unexpected_orders_schema_object_names(conn)
    if unexpected_objects:
        raise SafetyCheckError("unexpected_orders_schema_objects:" + ",".join(unexpected_objects))
    return contract


def _retired_table_inventory(conn: sqlite3.Connection, table: str) -> dict[str, object]:
    columns = sorted(_table_columns(conn, table))
    return {"columns": columns, "row_count": _scalar(conn, f"SELECT COUNT(*) FROM {_quote_identifier(table)}")}


def _retired_table_inventories(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    tables = _table_names(conn)
    return {table: _retired_table_inventory(conn, table) for table in sorted(RETIRED_TABLES & tables)}


def _require_retired_table_contract(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    unknown_h74_tables = sorted(name for name in tables if name.startswith("h74_") and name not in RETIRED_TABLES)
    if unknown_h74_tables:
        raise SafetyCheckError("unexpected_h74_prefixed_tables:" + ",".join(unknown_h74_tables))
    for table in sorted(RETIRED_TABLES & tables):
        columns = set(_table_columns(conn, table))
        contract = RETIRED_TABLE_CONTRACTS[table]
        missing = sorted(contract["required_columns"] - columns)
        unexpected = sorted(columns - contract["allowed_columns"])
        if missing or unexpected:
            raise SafetyCheckError(
                f"retired_table_schema_mismatch:{table}:missing={','.join(missing)}:unexpected={','.join(unexpected)}"
            )


def _update_length_prefixed(digest: hashlib._Hash, payload: bytes) -> None:
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _encode_sqlite_value(value: object) -> bytes:
    """Encode SQLite values with type and length boundaries independent of repr/locale."""
    if value is None:
        return b"N"
    if isinstance(value, bytes):
        return b"B" + len(value).to_bytes(8, "big") + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"T" + len(encoded).to_bytes(8, "big") + encoded
    if isinstance(value, int):
        return b"I" + str(value).encode("ascii")
    if isinstance(value, float):
        if math.isnan(value):
            return b"Fnan"
        return b"F" + struct.pack(">d", value)
    raise SafetyCheckError(f"unsupported_orders_hash_value_type:{type(value).__name__}")


def _table_rows_hash(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: list[str],
    order_by: str,
) -> str:
    """Hash all selected rows using explicit row and field boundaries."""
    digest = hashlib.sha256()
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    query = (
        f"SELECT {quoted_columns} FROM {_quote_identifier(table)} "
        f"ORDER BY {_quote_identifier(order_by)}"
    )
    for row in conn.execute(query):
        digest.update(b"R")
        for value in row:
            digest.update(b"V")
            _update_length_prefixed(digest, _encode_sqlite_value(value))
        digest.update(b"E")
    return digest.hexdigest()


def _orders_stats(conn: sqlite3.Connection, *, table: str, retained_columns: list[str]) -> dict[str, object]:
    columns = _table_columns(conn, table)
    order_by = "id" if "id" in columns else "client_order_id"
    quoted_table = _quote_identifier(table)

    def counts(column: str) -> dict[str, int]:
        if column not in columns:
            return {}
        return {
            "<NULL>" if row[0] is None else str(row[0]): int(row[1])
            for row in conn.execute(
                f"SELECT {_quote_identifier(column)}, COUNT(*) FROM {quoted_table} "
                f"GROUP BY {_quote_identifier(column)} ORDER BY {_quote_identifier(column)}"
            )
        }

    def status_count(status: str) -> int:
        if "status" not in columns:
            return 0
        return _scalar(conn, f"SELECT COUNT(*) FROM {quoted_table} WHERE status=?", (status,))

    return {
        "row_count": _scalar(conn, f"SELECT COUNT(*) FROM {quoted_table}"),
        "max_id": _scalar(conn, f"SELECT COALESCE(MAX(id), 0) FROM {quoted_table}") if "id" in columns else 0,
        "status_counts": counts("status"),
        "side_counts": counts("side"),
        "exchange_order_id_non_null_count": (
            _scalar(conn, f"SELECT COUNT(*) FROM {quoted_table} WHERE exchange_order_id IS NOT NULL")
            if "exchange_order_id" in columns
            else 0
        ),
        "submit_unknown_count": status_count("SUBMIT_UNKNOWN"),
        "recovery_required_count": status_count("RECOVERY_REQUIRED"),
        "accounting_pending_count": status_count("ACCOUNTING_PENDING"),
        "retained_columns_hash": _table_rows_hash(
            conn, table=table, columns=retained_columns, order_by=order_by
        ),
    }


def _require_sqlite_integrity(conn: sqlite3.Connection, *, source: str) -> None:
    integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
    if integrity_rows != [("ok",)]:
        raise SafetyCheckError(f"{source}_integrity_check_failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise SafetyCheckError(f"{source}_foreign_key_check_failed")


@contextmanager
def _path_manager_for_mode(mode: str) -> Iterator[PathManager]:
    previous_mode = os.environ.get("MODE")
    os.environ["MODE"] = mode
    try:
        manager = PathManager.from_env(PROJECT_ROOT)
        if mode == "live":
            validate_runtime_root_separation(manager.config)
        yield manager
    except PathPolicyError as exc:
        raise SafetyCheckError(f"path_manager_policy_error:{exc}") from exc
    finally:
        if previous_mode is None:
            os.environ.pop("MODE", None)
        else:
            os.environ["MODE"] = previous_mode


def _is_within(path: Path, root: Path) -> bool:
    return PathManager._is_within(path, root)


def _validate_paths(*, db_path: Path, backup_path: Path, mode: str) -> tuple[Path, Path, PathManager]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in ALLOWED_MODES:
        raise SafetyCheckError("invalid_mode")
    db_path = db_path.expanduser()
    backup_path = backup_path.expanduser()
    if not db_path.is_absolute() or not backup_path.is_absolute():
        raise SafetyCheckError("db_and_backup_paths_must_be_absolute")
    resolved_db = db_path.resolve()
    resolved_backup = backup_path.resolve()
    project_root = PROJECT_ROOT.resolve()
    if _is_within(resolved_db, project_root):
        raise SafetyCheckError("database_path_inside_repository")
    if _is_within(resolved_backup, project_root):
        raise SafetyCheckError("backup_path_inside_repository")
    if resolved_db == resolved_backup:
        raise SafetyCheckError("backup_path_must_differ_from_database")
    if not resolved_db.is_file():
        raise SafetyCheckError("database_path_missing")

    with _path_manager_for_mode(normalized_mode) as manager:
        canonical_db = manager.primary_db_path_for_mode(normalized_mode).resolve()
        expected_db_dir = canonical_db.parent
        configured_db_raw = os.getenv("DB_PATH", "").strip()
        configured_db = None
        if configured_db_raw:
            configured_db_candidate = Path(configured_db_raw).expanduser()
            if not configured_db_candidate.is_absolute():
                raise SafetyCheckError("configured_db_path_must_be_absolute")
            configured_db = configured_db_candidate.resolve()
        if configured_db is None and resolved_db != canonical_db:
            raise SafetyCheckError("database_mode_mismatch")
        if configured_db is not None and configured_db != resolved_db:
            raise SafetyCheckError("database_mode_mismatch")
        if not _is_within(resolved_db, expected_db_dir):
            raise SafetyCheckError("database_mode_mismatch")
        backup_bucket = (manager.config.backup_root / normalized_mode / "db").resolve()
        if not _is_within(resolved_backup, backup_bucket):
            if PathManager._contains_segment(resolved_backup, "live" if normalized_mode == "paper" else "paper"):
                raise SafetyCheckError("backup_mode_mismatch")
            raise SafetyCheckError("backup_path_outside_managed_db_bucket")
        if PathManager._contains_segment(resolved_db, "live" if normalized_mode == "paper" else "paper"):
            raise SafetyCheckError("database_mode_mismatch")
        if PathManager._contains_segment(resolved_backup, "live" if normalized_mode == "paper" else "paper"):
            raise SafetyCheckError("backup_mode_mismatch")
        return resolved_db, resolved_backup, manager


def _migration_checkpoint(stage: str) -> None:
    """Internal test seam; production callers never supply failure injection."""


def _rebuild_orders(conn: sqlite3.Connection, *, contract: dict[str, list[str]]) -> dict[str, object]:
    source_columns = _table_columns(conn, "orders")
    source_only_columns = set(source_columns) - set(_canonical_orders_columns())
    retired = sorted(RETIRED_ORDER_COLUMNS.intersection(source_only_columns))
    if set(retired) != source_only_columns:
        raise SafetyCheckError("orders_rebuild_removed_columns_contract_mismatch")
    canonical_sql, canonical_objects = _canonical_orders_sql()
    target_table = "orders__retired_schema_tmp"
    target_sql = canonical_sql.replace("CREATE TABLE IF NOT EXISTS orders", f"CREATE TABLE {target_table}", 1)
    if target_sql == canonical_sql:
        target_sql = canonical_sql.replace("CREATE TABLE orders", f"CREATE TABLE {target_table}", 1)
    if target_sql == canonical_sql:
        raise SafetyCheckError("canonical_orders_schema_unrecognized")
    template = sqlite3.connect(":memory:")
    try:
        template.execute(target_sql)
        target_columns = _table_columns(template, target_table)
    finally:
        template.close()
    retained_columns = [name for name in target_columns if name in source_columns]
    if not retained_columns:
        raise SafetyCheckError("canonical_orders_copy_columns_missing")
    before = _orders_stats(conn, table="orders", retained_columns=retained_columns)
    if not retired:
        return {
            "rebuilt": False,
            "removed_columns": [],
            **contract,
            "before": before,
            "after": before,
            "retained_columns": retained_columns,
            "retained_rows_hash": str(before["retained_columns_hash"]),
        }
    conn.execute(target_sql)
    quoted = ", ".join(_quote_identifier(name) for name in retained_columns)
    conn.execute(f"INSERT INTO {_quote_identifier(target_table)} ({quoted}) SELECT {quoted} FROM orders")
    copied = _orders_stats(conn, table=target_table, retained_columns=retained_columns)
    if copied != before:
        raise SafetyCheckError("orders_copy_retained_data_mismatch")
    _migration_checkpoint("after_orders_copy")
    _migration_checkpoint("before_orders_drop")
    conn.execute("DROP TABLE orders")
    conn.execute(f"ALTER TABLE {_quote_identifier(target_table)} RENAME TO orders")
    for schema_object in canonical_objects:
        conn.execute(schema_object.sql)
    source_objects = _orders_schema_objects(conn)
    expected_objects, expected_auto_indexes = _canonical_orders_schema_inventory()
    if source_objects != expected_objects:
        raise SafetyCheckError("orders_schema_objects_not_restored")
    if _orders_auto_indexes(conn) != expected_auto_indexes:
        raise SafetyCheckError("orders_auto_index_contract_mismatch")
    after = _orders_stats(conn, table="orders", retained_columns=retained_columns)
    if after != before:
        raise SafetyCheckError("orders_rebuild_retained_data_mismatch")
    return {
        "rebuilt": bool(retired),
        "removed_columns": retired,
        **contract,
        "before": before,
        "after": after,
        "retained_columns": retained_columns,
        "retained_rows_hash": str(before["retained_columns_hash"]),
    }


def inspect(conn: sqlite3.Connection) -> dict[str, object]:
    tables = _table_names(conn)
    retired_tables = sorted(RETIRED_TABLES & tables)
    retired_columns = (
        sorted(RETIRED_ORDER_COLUMNS.intersection(_table_columns(conn, "orders")))
        if "orders" in tables
        else []
    )
    if "orders" not in tables:
        return {
            "retired_tables": retired_tables,
            "retired_order_columns": retired_columns,
            "source_only_columns": [],
            "expected_retired_columns": [],
            "unexpected_source_columns": [],
            "removed_columns": [],
            "retired_table_inventory": _retired_table_inventories(conn),
        }
    return {
        "retired_tables": retired_tables,
        "retired_order_columns": retired_columns,
        "removed_columns": [],
        "retired_table_inventory": _retired_table_inventories(conn),
        **_orders_rebuild_contract(conn),
    }


def _validate_backup(
    *, conn: sqlite3.Connection, backup_path: Path, retained_columns: list[str]
) -> tuple[str, dict[str, object]]:
    if not backup_path.is_file() or backup_path.stat().st_size <= 0:
        raise SafetyCheckError("backup_integrity_check_failed")
    source_stats = _orders_stats(conn, table="orders", retained_columns=retained_columns)
    source_table_counts = {
        table: _scalar(conn, f"SELECT COUNT(*) FROM {_quote_identifier(table)}")
        for table in sorted(_table_names(conn))
        if not table.startswith("sqlite_")
    }
    backup_conn = sqlite3.connect(backup_path)
    try:
        _require_sqlite_integrity(backup_conn, source="backup")
        backup_stats = _orders_stats(backup_conn, table="orders", retained_columns=retained_columns)
        backup_table_counts = {
            table: _scalar(backup_conn, f"SELECT COUNT(*) FROM {_quote_identifier(table)}")
            for table in sorted(_table_names(backup_conn))
            if not table.startswith("sqlite_")
        }
    finally:
        backup_conn.close()
    if backup_table_counts != source_table_counts:
        raise SafetyCheckError("backup_table_row_count_mismatch")
    if backup_stats != source_stats:
        raise SafetyCheckError("backup_orders_hash_mismatch")
    return _sha256(backup_path), backup_stats


def run(
    *,
    db_path: Path,
    backup_path: Path,
    mode: str,
    apply: bool,
    confirmation: str,
    broker_local_converged: bool,
) -> dict[str, object]:
    db_path, backup_path, manager = _validate_paths(
        db_path=db_path, backup_path=backup_path, mode=mode
    )
    def evaluate(*, locked: bool) -> dict[str, object]:
        conn = sqlite3.connect(db_path)
        try:
            report = inspect(conn)
            report.update(
                {
                    "apply": bool(apply),
                    "db_path": str(db_path),
                    "backup_path": str(backup_path),
                    "run_lock_acquired": locked,
                }
            )
            if not apply:
                return {**report, "status": "dry_run", "backup_created": False, "database_modified": False}
            _require_retired_table_contract(conn)
            orders_contract = _require_orders_rebuild_contract(conn) if "orders" in _table_names(conn) else None
            if not report["retired_tables"] and not report["retired_order_columns"]:
                return {**report, "status": "already_clean", "backup_created": False, "database_modified": False}
            if confirmation != CONFIRMATION:
                raise SafetyCheckError("explicit_confirmation_required")
            _require_safe_stop(conn, db_path, broker_local_converged)
            _require_sqlite_integrity(conn, source="source_database")
            if backup_path.exists():
                raise SafetyCheckError("backup_path_already_exists")
            retained_columns = [name for name in _canonical_orders_columns() if name in _table_columns(conn, "orders")]
            if not retained_columns:
                raise SafetyCheckError("canonical_orders_copy_columns_missing")
            manager.ensure_parent_dir(backup_path)
            with sqlite3.connect(backup_path) as backup_conn:
                conn.backup(backup_conn)
            backup_sha256, backup_orders = _validate_backup(conn=conn, backup_path=backup_path, retained_columns=retained_columns)
            conn.execute("BEGIN IMMEDIATE")
            try:
                if orders_contract is None:
                    raise SafetyCheckError("orders_table_missing")
                report["orders"] = _rebuild_orders(conn, contract=orders_contract)
                for key in ("source_only_columns", "expected_retired_columns", "unexpected_source_columns", "removed_columns"):
                    report[key] = report["orders"][key]
                _migration_checkpoint("before_retired_table_drop")
                removed_tables: list[str] = []
                for table in list(report["retired_tables"]):
                    conn.execute(f"DROP TABLE {_quote_identifier(str(table))}")
                    removed_tables.append(str(table))
                report["removed_tables"] = removed_tables
                _migration_checkpoint("before_final_integrity_check")
                _require_sqlite_integrity(conn, source="post_migration")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {
                **report,
                "status": "applied",
                "backup_created": True,
                "database_modified": True,
                "backup_sha256": backup_sha256,
                "backup_orders": backup_orders,
                "foreign_key_check": "ok",
                "integrity_check": "ok",
            }
        finally:
            conn.close()

    if not apply:
        return evaluate(locked=False)
    try:
        with acquire_run_lock(manager.run_lock_path_for_mode(mode)):
            return evaluate(locked=True)
    except RunLockError as exc:
        raise SafetyCheckError("migration_run_lock_unavailable") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(ALLOWED_MODES), required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="perform migration; default is dry-run")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--broker-local-converged", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            db_path=args.db,
            backup_path=args.backup,
            mode=str(args.mode),
            apply=bool(args.apply),
            confirmation=str(args.confirm),
            broker_local_converged=bool(args.broker_local_converged),
        )
    except (SafetyCheckError, sqlite3.Error) as exc:
        print(f"migration_refused:{exc}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
