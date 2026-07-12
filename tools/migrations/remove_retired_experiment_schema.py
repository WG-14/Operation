#!/usr/bin/env python3
"""Retire H74-era schema only after an operator establishes a safe stop point.

This command is intentionally not part of startup migrations.  References to
retired names in this file are legacy retired-schema migration only; not an
active runtime feature.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterable


CONFIRMATION = "REMOVE_RETIRED_EXPERIMENT_SCHEMA"
# legacy retired-schema migration only; not an active runtime feature
RETIRED_ORDER_COLUMNS = frozenset(
    {
        "daily_participation_policy_hash",
        "daily_count_snapshot_hash",
        "participation_decision_hash",
        "daily_participation_kst_day",
        "daily_participation_fallback_mode",
    }
)
RETIRED_TABLE_PREFIXES = ("h74_",)
RETIRED_TABLES = frozenset({"daily_participation_claims"})
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
    for status in ("SUBMIT_UNKNOWN", "RECOVERY_REQUIRED", "ACCOUNTING_PENDING"):
        count = _scalar(conn, "SELECT COUNT(*) FROM orders WHERE status=?", (status,))
        if count:
            raise SafetyCheckError(f"{status.lower()}_count={count}")
    if not broker_local_converged:
        raise SafetyCheckError("broker_local_position_convergence_operator_attestation_required")


def _canonical_orders_sql() -> tuple[str, list[str]]:
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
        indexes = [
            str(item[0])
            for item in canonical.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='orders' AND sql IS NOT NULL"
            ).fetchall()
        ]
        return str(row[0]), indexes
    finally:
        canonical.close()


def _orders_hash(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in conn.execute("SELECT client_order_id, status, side, qty_req, qty_filled FROM orders ORDER BY client_order_id"):
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _rebuild_orders(conn: sqlite3.Connection) -> dict[str, object]:
    source_columns = _table_columns(conn, "orders")
    retired = sorted(RETIRED_ORDER_COLUMNS.intersection(source_columns))
    if not retired:
        return {"rebuilt": False, "removed_columns": [], "row_count": _scalar(conn, "SELECT COUNT(*) FROM orders")}
    canonical_sql, canonical_indexes = _canonical_orders_sql()
    target_table = "orders__retired_schema_tmp"
    target_sql = canonical_sql.replace("CREATE TABLE IF NOT EXISTS orders", f"CREATE TABLE {target_table}", 1)
    if target_sql == canonical_sql:
        target_sql = canonical_sql.replace("CREATE TABLE orders", f"CREATE TABLE {target_table}", 1)
    if target_sql == canonical_sql:
        raise SafetyCheckError("canonical_orders_schema_unrecognized")
    # Canonical names are stable and come from the schema itself; obtain them from a disposable connection.
    template = sqlite3.connect(":memory:")
    try:
        template.execute(target_sql)
        target_columns = _table_columns(template, target_table)
    finally:
        template.close()
    copy_columns = [name for name in target_columns if name in source_columns]
    if not copy_columns:
        raise SafetyCheckError("canonical_orders_copy_columns_missing")
    before_count = _scalar(conn, "SELECT COUNT(*) FROM orders")
    before_hash = _orders_hash(conn)
    conn.execute(target_sql)
    quoted = ", ".join(f'"{name}"' for name in copy_columns)
    conn.execute(f"INSERT INTO {target_table} ({quoted}) SELECT {quoted} FROM orders")
    copied_count = _scalar(conn, f"SELECT COUNT(*) FROM {target_table}")
    if copied_count != before_count:
        raise SafetyCheckError(f"orders_copy_row_count_mismatch:{before_count}!={copied_count}")
    copied_hash = hashlib.sha256()
    for row in conn.execute(
        f"SELECT client_order_id, status, side, qty_req, qty_filled FROM {target_table} ORDER BY client_order_id"
    ):
        copied_hash.update(repr(tuple(row)).encode("utf-8"))
        copied_hash.update(b"\n")
    if copied_hash.hexdigest() != before_hash:
        raise SafetyCheckError("orders_copy_core_hash_mismatch")
    conn.execute("DROP TABLE orders")
    conn.execute(f"ALTER TABLE {target_table} RENAME TO orders")
    for index_sql in canonical_indexes:
        conn.execute(index_sql)
    return {"rebuilt": True, "removed_columns": retired, "row_count": before_count, "core_hash": before_hash}


def inspect(conn: sqlite3.Connection) -> dict[str, object]:
    tables = _table_names(conn)
    retired_tables = sorted(name for name in tables if name in RETIRED_TABLES or name.startswith(RETIRED_TABLE_PREFIXES))
    retired_columns = sorted(RETIRED_ORDER_COLUMNS.intersection(_table_columns(conn, "orders"))) if "orders" in tables else []
    return {"retired_tables": retired_tables, "retired_order_columns": retired_columns}


def run(*, db_path: Path, backup_path: Path, apply: bool, confirmation: str, broker_local_converged: bool) -> dict[str, object]:
    if not db_path.is_absolute() or not backup_path.is_absolute():
        raise SafetyCheckError("db_and_backup_paths_must_be_absolute")
    if db_path.resolve() == backup_path.resolve():
        raise SafetyCheckError("backup_path_must_differ_from_database")
    if not db_path.is_file():
        raise SafetyCheckError("database_path_missing")
    conn = sqlite3.connect(db_path)
    try:
        report = inspect(conn)
        report.update({"apply": apply, "db_path": str(db_path), "backup_path": str(backup_path)})
        if not apply:
            return report
        if confirmation != CONFIRMATION:
            raise SafetyCheckError("explicit_confirmation_required")
        _require_safe_stop(conn, db_path, broker_local_converged)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup_path) as backup_conn:
            conn.backup(backup_conn)
        report["backup_sha256"] = _sha256(backup_path)
        conn.execute("BEGIN IMMEDIATE")
        try:
            report["orders"] = _rebuild_orders(conn)
            for table in list(report["retired_tables"]):
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if fk_errors or integrity != "ok":
                raise SafetyCheckError("post_migration_integrity_check_failed")
            conn.commit()
            report["foreign_key_check"] = "ok"
            report["integrity_check"] = str(integrity)
            return report
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="perform migration; default is dry-run")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--broker-local-converged", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            db_path=args.db.expanduser().resolve(),
            backup_path=args.backup.expanduser().resolve(),
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
