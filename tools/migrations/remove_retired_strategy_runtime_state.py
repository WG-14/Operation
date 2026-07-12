#!/usr/bin/env python3
"""Offline cleanup of runtime state left by retired strategies.

This command is deliberately not a startup migration or reconcile action.
It only removes retired virtual target rows and, with an additional explicit
flag, a flat/converged pair's shared target state.  Ledger and audit evidence
is never modified.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterable, Iterator

from bithumb_bot.paths import ALLOWED_MODES, PathManager, PathPolicyError, validate_runtime_root_separation
from bithumb_bot.run_lock import RunLockError, acquire_run_lock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION = "REMOVE_RETIRED_STRATEGY_RUNTIME_STATE"
FLAT_TOLERANCE = 1e-12
UNSAFE_ORDER_STATUSES = frozenset(
    {"PENDING_SUBMIT", "NEW", "PARTIAL", "SUBMIT_UNKNOWN", "ACCOUNTING_PENDING", "RECOVERY_REQUIRED", "CANCEL_REQUESTED"}
)
PROTECTED_TABLES = (
    "orders", "fills", "trades", "trade_lifecycles", "order_events",
    "broker_fill_observations", "execution_quality_events",
)


class SafetyCheckError(RuntimeError):
    pass


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _scalar(conn: sqlite3.Connection, query: str, params: Iterable[object] = ()) -> int:
    row = conn.execute(query, tuple(params)).fetchone()
    return int(row[0] if row else 0)


def _holders(db_path: Path) -> list[int]:
    if os.name != "posix" or not Path("/proc").is_dir():
        raise SafetyCheckError("active_process_check_unavailable")
    resolved = db_path.resolve()
    holders: list[int] = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                try:
                    if fd.resolve() == resolved and int(pid_dir.name) != os.getpid():
                        holders.append(int(pid_dir.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return holders


def _require_integrity(conn: sqlite3.Connection, source: str) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA integrity_check").fetchall()] != [("ok",)]:
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


def _validate_paths(*, db_path: Path, backup_path: Path, mode: str) -> tuple[Path, Path, PathManager]:
    if mode not in ALLOWED_MODES:
        raise SafetyCheckError("invalid_mode")
    if not db_path.expanduser().is_absolute() or not backup_path.expanduser().is_absolute():
        raise SafetyCheckError("db_and_backup_paths_must_be_absolute")
    db_path, backup_path = db_path.expanduser().resolve(), backup_path.expanduser().resolve()
    if PathManager._is_within(db_path, PROJECT_ROOT.resolve()) or PathManager._is_within(backup_path, PROJECT_ROOT.resolve()):
        raise SafetyCheckError("runtime_path_inside_repository")
    if db_path == backup_path or not db_path.is_file():
        raise SafetyCheckError("backup_path_must_differ_from_database" if db_path == backup_path else "database_path_missing")
    with _path_manager_for_mode(mode) as manager:
        configured_db_raw = os.getenv("DB_PATH", "").strip()
        configured_db = None
        if configured_db_raw:
            candidate = Path(configured_db_raw).expanduser()
            if not candidate.is_absolute():
                raise SafetyCheckError("configured_db_path_must_be_absolute")
            configured_db = candidate.resolve()
        canonical_db = manager.primary_db_path_for_mode(mode).resolve()
        if (configured_db is None and db_path != canonical_db) or (configured_db is not None and db_path != configured_db):
            raise SafetyCheckError("database_mode_mismatch")
        if not PathManager._is_within(db_path, canonical_db.parent):
            raise SafetyCheckError("database_mode_mismatch")
        backup_bucket = (manager.config.backup_root / mode / "db").resolve()
        if not PathManager._is_within(backup_path, backup_bucket):
            raise SafetyCheckError("backup_path_outside_managed_db_bucket")
        wrong_mode = "live" if mode == "paper" else "paper"
        if PathManager._contains_segment(db_path, wrong_mode) or PathManager._contains_segment(backup_path, wrong_mode):
            raise SafetyCheckError("runtime_mode_mismatch")
        return db_path, backup_path, manager


def _row_hash(conn: sqlite3.Connection, table: str) -> str:
    digest = hashlib.sha256()
    columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")]
    order_by = "id" if "id" in columns else ", ".join(_quote_identifier(column) for column in columns)
    selected = ", ".join(_quote_identifier(column) for column in columns)
    for row in conn.execute(f"SELECT {selected} FROM {_quote_identifier(table)} ORDER BY {order_by}"):
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _protected_inventory(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    tables = _table_names(conn)
    missing = sorted(set(PROTECTED_TABLES) - tables)
    if missing:
        raise SafetyCheckError("protected_table_missing:" + ",".join(missing))
    return {table: {"row_count": _scalar(conn, f"SELECT COUNT(*) FROM {_quote_identifier(table)}"), "hash": _row_hash(conn, table)} for table in PROTECTED_TABLES}


def _retired_virtual_rows(conn: sqlite3.Connection, pair: str) -> list[dict[str, object]]:
    if "strategy_virtual_target_state" not in _table_names(conn):
        raise SafetyCheckError("strategy_virtual_target_state_missing")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT * FROM strategy_virtual_target_state
           WHERE pair=? AND (strategy_name='daily_participation_sma'
              OR strategy_instance_id LIKE 'daily_participation_sma:%'
              OR strategy_instance_id LIKE 'h74%')
           ORDER BY strategy_instance_id, interval, scope_key_hash""",
        (pair,),
    ).fetchall()
    return [dict(row) for row in rows]


def _target_row(conn: sqlite3.Connection, pair: str) -> dict[str, object] | None:
    if "target_position_state" not in _table_names(conn):
        raise SafetyCheckError("target_position_state_missing")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM target_position_state WHERE pair=?", (pair,)).fetchone()
    return None if row is None else dict(row)


def _safety_snapshot(conn: sqlite3.Connection, pair: str) -> dict[str, object]:
    tables = _table_names(conn)
    if not {"portfolio", "open_position_lots", "orders"}.issubset(tables):
        raise SafetyCheckError("runtime_safety_table_missing")
    portfolio_row = conn.execute("SELECT asset_qty FROM portfolio WHERE id=1").fetchone()
    portfolio_asset_qty = float(portfolio_row[0] or 0.0) if portfolio_row else 0.0
    open_lots = _scalar(
        conn,
        """SELECT COALESCE(SUM(executable_lot_count), 0) FROM open_position_lots
           WHERE pair=? AND position_state='open_exposure'""",
        (pair,),
    )
    statuses = sorted(UNSAFE_ORDER_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    risky = _scalar(conn, f"SELECT COUNT(*) FROM orders WHERE pair=? AND status IN ({placeholders})", (pair, *statuses))
    return {"portfolio_asset_qty": portfolio_asset_qty, "open_position_lot_count": open_lots, "risky_order_count": risky}


def inspect(conn: sqlite3.Connection, *, pair: str) -> dict[str, object]:
    safety = _safety_snapshot(conn, pair)
    virtual_rows = _retired_virtual_rows(conn, pair)
    target = _target_row(conn, pair)
    return {
        "pair": pair,
        **safety,
        "retired_virtual_target_state_count": len(virtual_rows),
        "retired_virtual_target_state_rows": virtual_rows,
        "pair_target_position_state_present": target is not None,
        "pair_target_position_state": target,
        "protected_table_counts": _protected_inventory(conn),
    }


def _require_safe_apply(conn: sqlite3.Connection, *, db_path: Path, pair: str, broker_local_converged: bool, clear_pair_target_state: bool) -> None:
    if _holders(db_path):
        raise SafetyCheckError("database_has_active_process_holder")
    snapshot = _safety_snapshot(conn, pair)
    if int(snapshot["risky_order_count"]):
        raise SafetyCheckError(f"unresolved_open_order_count={snapshot['risky_order_count']}")
    if not broker_local_converged:
        raise SafetyCheckError("broker_local_position_convergence_operator_attestation_required")
    if clear_pair_target_state:
        if abs(float(snapshot["portfolio_asset_qty"])) > FLAT_TOLERANCE:
            raise SafetyCheckError("pair_target_state_requires_flat_portfolio")
        if int(snapshot["open_position_lot_count"]):
            raise SafetyCheckError("pair_target_state_requires_zero_open_executable_lots")


def run(*, db_path: Path, backup_path: Path, mode: str, pair: str, apply: bool, confirmation: str, broker_local_converged: bool, clear_pair_target_state: bool) -> dict[str, object]:
    pair = str(pair or "").strip().upper()
    if not pair:
        raise SafetyCheckError("pair_required")
    db_path, backup_path, manager = _validate_paths(db_path=db_path, backup_path=backup_path, mode=mode)

    def evaluate(*, locked: bool) -> dict[str, object]:
        conn = sqlite3.connect(db_path)
        try:
            report = inspect(conn, pair=pair)
            report.update({"apply": apply, "db_path": str(db_path), "backup_path": str(backup_path), "run_lock_acquired": locked})
            changes_needed = bool(report["retired_virtual_target_state_count"]) or (clear_pair_target_state and report["pair_target_position_state_present"])
            if not apply:
                return {**report, "status": "dry_run", "database_modified": False, "backup_created": False, "backup_sha256": None}
            if not changes_needed:
                return {**report, "status": "already_clean", "database_modified": False, "backup_created": False, "backup_sha256": None}
            if confirmation != CONFIRMATION:
                raise SafetyCheckError("explicit_confirmation_required")
            _require_safe_apply(conn, db_path=db_path, pair=pair, broker_local_converged=broker_local_converged, clear_pair_target_state=clear_pair_target_state)
            _require_integrity(conn, "source_database")
            if backup_path.exists():
                raise SafetyCheckError("backup_path_already_exists")
            manager.ensure_parent_dir(backup_path)
            with sqlite3.connect(backup_path) as backup_conn:
                conn.backup(backup_conn)
            backup_sha256 = _sha256(backup_path)
            backup_conn = sqlite3.connect(backup_path)
            try:
                _require_integrity(backup_conn, "backup")
                if _protected_inventory(backup_conn) != report["protected_table_counts"]:
                    raise SafetyCheckError("backup_protected_table_contract_mismatch")
            finally:
                backup_conn.close()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """DELETE FROM strategy_virtual_target_state WHERE pair=?
                       AND (strategy_name='daily_participation_sma'
                            OR strategy_instance_id LIKE 'daily_participation_sma:%'
                            OR strategy_instance_id LIKE 'h74%')""",
                    (pair,),
                )
                target_removed = False
                if clear_pair_target_state and report["pair_target_position_state_present"]:
                    conn.execute("DELETE FROM target_position_state WHERE pair=?", (pair,))
                    target_removed = True
                if _protected_inventory(conn) != report["protected_table_counts"]:
                    raise SafetyCheckError("protected_table_contract_mismatch")
                _require_integrity(conn, "post_migration")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {
                **report,
                "status": "applied",
                "database_modified": True,
                "backup_created": True,
                "backup_sha256": backup_sha256,
                "pair_target_position_state_removed": target_removed,
                "integrity_check": "ok",
                "foreign_key_check": "ok",
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
    parser.add_argument("--pair", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--broker-local-converged", action="store_true")
    parser.add_argument("--clear-pair-target-state", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        print(run(db_path=args.db, backup_path=args.backup, mode=args.mode, pair=args.pair, apply=args.apply, confirmation=args.confirm, broker_local_converged=args.broker_local_converged, clear_pair_target_state=args.clear_pair_target_state))
    except (SafetyCheckError, sqlite3.Error) as exc:
        print(f"runtime_state_cleanup_refused:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
