"""Shared, offline-only safety engine for removed-strategy retirement.

This module owns every stateful part of the retirement operation.  The CLI is
intentionally a thin JSON adapter so planning and applying cannot drift.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
from typing import Any, Iterator, Sequence

from bithumb_bot.paths import ALLOWED_MODES, PathManager, PathPolicyError, validate_runtime_root_separation
from bithumb_bot.run_lock import RunLockError, acquire_run_lock, read_run_lock_status
from bithumb_bot.storage_io import write_text_atomic


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_SCHEMA_VERSION = 1
TOOL_VERSION = "1"
CONFIRMATION = "RETIRE_REMOVED_STRATEGY"
FLAT_TOLERANCE = 1e-12
RETIRED_ORDER_COLUMNS = frozenset({
    "h74_entry_plan_client_order_id", "h74_position_ownership_contract_hash",
    "h74_position_ownership_contract", "daily_participation_policy_hash",
    "daily_count_snapshot_hash", "participation_decision_hash",
    "daily_participation_kst_day", "daily_participation_fallback_mode",
})
RETIRED_TABLES = frozenset({"h74_cycle_state", "daily_participation_claims"})
RETIRED_TABLE_CONTRACTS = {
    "h74_cycle_state": {
        "required": frozenset({"cycle_id", "authority_hash", "strategy_instance_id", "pair", "state", "entry_client_order_id", "exit_client_order_id", "entry_filled_ts", "scheduled_exit_ts", "acquired_qty", "sold_qty", "locked_exit_qty", "unauthorized_intermediate_order_count", "updated_ts"}),
        "allowed": frozenset({"cycle_id", "authority_hash", "strategy_instance_id", "pair", "state", "entry_client_order_id", "exit_client_order_id", "entry_filled_ts", "scheduled_exit_ts", "acquired_qty", "sold_qty", "locked_exit_qty", "unauthorized_intermediate_order_count", "updated_ts", "contract_hash", "h74_entry_plan_client_order_id"}),
    },
    "daily_participation_claims": {
        "required": frozenset({"id", "strategy_instance_id", "pair", "kst_day", "participation_policy_hash", "daily_count_snapshot_hash", "participation_decision_hash", "fallback_mode", "client_order_id", "status", "created_ts", "updated_ts"}),
        "allowed": frozenset({"id", "strategy_instance_id", "pair", "kst_day", "participation_policy_hash", "daily_count_snapshot_hash", "participation_decision_hash", "fallback_mode", "client_order_id", "status", "retry_allowed", "created_ts", "updated_ts"}),
    },
}
PROTECTED_TABLES = (
    "orders", "fills", "trades", "trade_lifecycles", "order_events",
    "broker_fill_observations", "execution_quality_events",
)
UNSAFE_ORDER_STATUSES = frozenset({
    "PENDING_SUBMIT", "NEW", "PARTIAL", "SUBMIT_UNKNOWN", "ACCOUNTING_PENDING",
    "RECOVERY_REQUIRED", "CANCEL_REQUESTED",
})


class SafetyCheckError(RuntimeError):
    """Expected fail-closed operational refusal."""


@dataclass
class ApplyProgress:
    """Durable-in-memory account of the offline apply boundary reached."""

    phase: str = "preflight"
    backup_path: str | None = None
    backup_created: bool = False
    backup_verified: bool = False
    backup_sha256: str | None = None
    transaction_started: bool = False
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None
    commit_started: bool = False
    transaction_committed: bool = False
    commit_outcome: str = "not_started"
    foreign_keys_disabled: bool = False
    foreign_keys_reenabled: bool | None = None
    post_commit_verified: bool = False


class RetirementApplyError(SafetyCheckError):
    """An apply failure whose recovery state must be reported to an operator."""

    def __init__(
        self,
        reason_code: str,
        *,
        progress: ApplyProgress,
        changed_fields: tuple[str, ...] = (),
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.progress = progress
        self.changed_fields = changed_fields
        self.original_error = original_error

    @property
    def status(self) -> str:
        if self.progress.transaction_committed:
            return "applied_verification_failed"
        if self.progress.commit_started:
            return "apply_commit_outcome_unknown"
        if self.progress.transaction_started:
            if self.progress.rollback_succeeded is True:
                return "apply_failed_rolled_back"
            return "apply_outcome_unknown"
        return "apply_failed_before_transaction"

    @property
    def exit_code(self) -> int:
        return 3 if self.status in {"apply_failed_before_transaction", "apply_failed_rolled_back"} else 4

    def as_payload(self) -> dict[str, object]:
        status = self.status
        if status == "apply_failed_before_transaction":
            database_modified: bool | None = False
            action = "stop; inspect the backup; do not reuse the same backup path"
        elif status == "apply_failed_rolled_back":
            database_modified = False
            action = "keep the backup; inspect the failure; create a new plan and unique backup path before retry"
        elif status == "apply_outcome_unknown":
            database_modified = None
            action = "keep the service stopped; compare the DB with the verified backup; restore only through the approved recovery procedure"
        elif status == "apply_commit_outcome_unknown":
            database_modified = None
            action = "do not rerun; keep the service stopped; inspect the live DB and verified backup"
        else:
            database_modified = True
            action = "keep the service stopped; do not rerun; inspect or restore from the verified pre-change backup"
        payload: dict[str, object] = {
            "status": status,
            "reason_code": self.reason_code,
            "phase": self.progress.phase,
            "backup_created": self.progress.backup_created,
            "backup_verified": self.progress.backup_verified,
            "backup_sha256": self.progress.backup_sha256,
            "database_modified": database_modified,
            "transaction_started": self.progress.transaction_started,
            "transaction_committed": self.progress.transaction_committed,
            "commit_outcome": self.progress.commit_outcome,
            "rollback_attempted": self.progress.rollback_attempted,
            "rollback_succeeded": self.progress.rollback_succeeded,
            "foreign_keys_reenabled": self.progress.foreign_keys_reenabled,
            "post_commit_verified": self.progress.post_commit_verified,
            "recovery_required": True,
            "recommended_action": action,
        }
        if self.progress.backup_path is not None:
            payload["backup_path"] = self.progress.backup_path
        if self.changed_fields:
            payload["changed_fields"] = list(self.changed_fields)
        return payload


@dataclass(frozen=True)
class RetirementPlan:
    schema_version: int
    tool_version: str
    mode: str
    pair: str
    db_path: str
    backup_path: str
    source_db_sha256: str
    target_state_action: str
    retired_order_columns: tuple[str, ...]
    retired_tables: tuple[str, ...]
    retired_virtual_state_keys: tuple[dict[str, object], ...]
    pair_target_state_present: bool
    pair_target_state_hash: str | None
    protected_inventory: dict[str, object]
    safety_snapshot: dict[str, object]
    schema_inventory: dict[str, object]
    actions: tuple[dict[str, object], ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    status: str
    plan_hash: str


def canonical_json(payload: object) -> str:
    if is_dataclass(payload):
        payload = asdict(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def plan_payload(plan: RetirementPlan, *, include_hash: bool = True) -> dict[str, object]:
    payload = asdict(plan)
    if not include_hash:
        payload.pop("plan_hash", None)
    return payload


def compute_plan_hash(plan: RetirementPlan) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(plan_payload(plan, include_hash=False)).encode("utf-8")).hexdigest()


def write_plan(path: Path, plan: RetirementPlan) -> None:
    output = path.expanduser()
    if not output.is_absolute():
        raise SafetyCheckError("plan_output_path_must_be_absolute")
    output = output.resolve()
    if PathManager._is_within(output, PROJECT_ROOT.resolve()):
        raise SafetyCheckError("plan_output_path_inside_repository")
    with _manager(plan.mode) as manager:
        reports = (manager.data_dir_for_mode(plan.mode) / "reports").resolve()
        if not PathManager._is_within(output, reports):
            raise SafetyCheckError("plan_output_path_outside_managed_reports_bucket")
    write_text_atomic(output, canonical_json(plan_payload(plan)) + "\n")


def load_plan(path: Path) -> RetirementPlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyCheckError("retirement_plan_unreadable") from exc
    if not isinstance(raw, dict):
        raise SafetyCheckError("retirement_plan_invalid")
    try:
        for key in ("retired_order_columns", "retired_tables", "retired_virtual_state_keys", "actions", "blockers", "warnings"):
            if key in raw:
                raw[key] = tuple(raw[key])
        plan = RetirementPlan(**raw)
    except TypeError as exc:
        raise SafetyCheckError("retirement_plan_invalid") from exc
    if plan.schema_version != PLAN_SCHEMA_VERSION or plan.tool_version != TOOL_VERSION:
        raise SafetyCheckError("retirement_plan_version_unsupported")
    if not plan.plan_hash or compute_plan_hash(plan) != plan.plan_hash:
        raise SafetyCheckError("retirement_plan_hash_invalid")
    return plan


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _user_table_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = list(conn.execute(f"PRAGMA table_info({_quote(table)})"))
    return [str(row[1]) for row in sorted(rows, key=lambda row: int(row[5])) if int(row[5])]


def _count(conn: sqlite3.Connection, sql: str, values: Sequence[object] = ()) -> int:
    row = conn.execute(sql, tuple(values)).fetchone()
    return int(row[0]) if row else 0


def _encode_value(value: object) -> bytes:
    """A typed, length-delimited SQLite encoder independent of repr and JSON."""
    if value is None:
        return b"N"
    if isinstance(value, bool):  # sqlite returns ints, but reject ambiguity if a caller supplies bool.
        return b"I" + (b"1" if value else b"0")
    if isinstance(value, int):
        return b"I" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"Fnan" if math.isnan(value) else b"F" + struct.pack(">d", value)
    if isinstance(value, str):
        return b"T" + value.encode("utf-8")
    if isinstance(value, bytes):
        return b"B" + value
    raise SafetyCheckError(f"unsupported_hash_value_type:{type(value).__name__}")


def _framed(digest: Any, marker: bytes, value: bytes) -> None:
    digest.update(marker)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def table_row_hash(conn: sqlite3.Connection, table: str, *, columns: list[str] | None = None) -> str:
    selected = columns or _columns(conn, table)
    if not selected:
        raise SafetyCheckError(f"table_column_inventory_missing:{table}")
    ordering = _primary_key_columns(conn, table) or selected
    fields = ", ".join(_quote(column) for column in selected)
    order_by = ", ".join(_quote(column) for column in ordering)
    digest = hashlib.sha256()
    for column in selected:
        _framed(digest, b"C", column.encode("utf-8"))
    for row in conn.execute(f"SELECT {fields} FROM {_quote(table)} ORDER BY {order_by}"):
        digest.update(b"R")
        for value in row:
            _framed(digest, b"V", _encode_value(value))
        digest.update(b"E")
    return digest.hexdigest()


def _row_hash(values: Sequence[object], columns: Sequence[str]) -> str:
    digest = hashlib.sha256()
    digest.update(b"R")
    for column, value in zip(columns, values, strict=True):
        _framed(digest, b"C", column.encode("utf-8"))
        _framed(digest, b"V", _encode_value(value))
    digest.update(b"E")
    return digest.hexdigest()


def _require_integrity(conn: sqlite3.Connection, source: str) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SafetyCheckError(f"{source}_integrity_check_failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise SafetyCheckError(f"{source}_foreign_key_check_failed")


def _foreign_key_rows(conn: sqlite3.Connection, table: str) -> tuple[dict[str, object], ...]:
    rows = [
        {
            "child_table": table,
            "foreign_key_id": int(row[0]),
            "sequence": int(row[1]),
            "from_column": str(row[3]),
            "parent_table": str(row[2]),
            "to_column": str(row[4]),
            "on_update": str(row[5]),
            "on_delete": str(row[6]),
            "match": str(row[7]),
        }
        for row in conn.execute(f"PRAGMA foreign_key_list({_quote(table)})")
    ]
    return tuple(sorted(rows, key=lambda item: (str(item["child_table"]), int(item["foreign_key_id"]), int(item["sequence"]))))


def orders_foreign_key_inventory(conn: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    """Return every user-table FK relationship that references ``orders``."""
    return tuple(
        item
        for table in _user_table_names(conn)
        for item in _foreign_key_rows(conn, table)
        if item["parent_table"] == "orders"
    )


def _holders(db_path: Path) -> list[int]:
    if os.name != "posix" or not Path("/proc").is_dir():
        raise SafetyCheckError("active_process_check_unavailable")
    resolved, found = db_path.resolve(), []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if any(fd.resolve() == resolved for fd in (process / "fd").iterdir()):
                found.append(int(process.name))
        except OSError:
            continue
    return sorted(found)


@contextmanager
def _manager(mode: str) -> Iterator[PathManager]:
    previous = os.environ.get("MODE")
    os.environ["MODE"] = mode
    try:
        manager = PathManager.from_env(PROJECT_ROOT)
        if mode == "live":
            validate_runtime_root_separation(manager.config)
        yield manager
    except PathPolicyError as exc:
        raise SafetyCheckError("path_manager_policy_error") from exc
    finally:
        if previous is None:
            os.environ.pop("MODE", None)
        else:
            os.environ["MODE"] = previous


def validate_paths(*, mode: str, db_path: Path, backup_path: Path) -> tuple[Path, Path, PathManager]:
    mode = str(mode).strip().lower()
    if mode not in ALLOWED_MODES:
        raise SafetyCheckError("invalid_mode")
    if not db_path.expanduser().is_absolute() or not backup_path.expanduser().is_absolute():
        raise SafetyCheckError("db_and_backup_paths_must_be_absolute")
    db_path, backup_path = db_path.expanduser().resolve(), backup_path.expanduser().resolve()
    if PathManager._is_within(db_path, PROJECT_ROOT.resolve()) or PathManager._is_within(backup_path, PROJECT_ROOT.resolve()):
        raise SafetyCheckError("runtime_path_inside_repository")
    if db_path == backup_path:
        raise SafetyCheckError("backup_path_must_differ_from_database")
    if not db_path.is_file():
        raise SafetyCheckError("database_path_missing")
    with _manager(mode) as manager:
        configured = os.getenv("DB_PATH", "").strip()
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                raise SafetyCheckError("configured_db_path_must_be_absolute")
            expected_db = candidate.resolve()
        else:
            expected_db = manager.primary_db_path_for_mode(mode).resolve()
        if db_path != expected_db or not PathManager._is_within(db_path, manager.primary_db_path_for_mode(mode).resolve().parent):
            raise SafetyCheckError("database_mode_mismatch")
        backup_root = (manager.config.backup_root / mode / "db").resolve()
        if not PathManager._is_within(backup_path, backup_root):
            raise SafetyCheckError("backup_path_outside_managed_db_bucket")
        other = "live" if mode == "paper" else "paper"
        if PathManager._contains_segment(db_path, other) or PathManager._contains_segment(backup_path, other):
            raise SafetyCheckError("runtime_mode_mismatch")
        return db_path, backup_path, manager


def _canonical_orders_sql() -> tuple[str, list[tuple[str, str, str]]]:
    from bithumb_bot.db_core import ensure_schema
    canonical = sqlite3.connect(":memory:")
    try:
        ensure_schema(canonical)
        row = canonical.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'").fetchone()
        if row is None or not row[0]:
            raise SafetyCheckError("canonical_orders_schema_missing")
        return str(row[0]), _orders_objects(canonical)
    finally:
        canonical.close()


def _orders_objects(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [(str(row[0]), str(row[1]), str(row[2])) for row in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='orders' AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name"
    )]


def _auto_indexes(conn: sqlite3.Connection) -> list[tuple[str, tuple[object, ...]]]:
    result: list[tuple[str, tuple[object, ...]]] = []
    for row in conn.execute("PRAGMA index_list(orders)"):
        name = str(row[1])
        definition = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if definition is None or definition[0] is not None:
            continue
        columns = tuple(str(item[2]) for item in conn.execute(f"PRAGMA index_info({_quote(name)})"))
        result.append((name, (int(row[2]), str(row[3]), int(row[4]), columns)))
    return sorted(result)


def _canonical_orders_columns() -> list[str]:
    sql, _ = _canonical_orders_sql()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(sql)
        return _columns(conn, "orders")
    finally:
        conn.close()


def _canonical_objects() -> tuple[list[tuple[str, str, str]], list[tuple[str, tuple[object, ...]]]]:
    from bithumb_bot.db_core import ensure_schema
    conn = sqlite3.connect(":memory:")
    try:
        ensure_schema(conn)
        return _orders_objects(conn), _auto_indexes(conn)
    finally:
        conn.close()


def _orders_contract(conn: sqlite3.Connection) -> dict[str, object]:
    if "orders" not in _table_names(conn):
        raise SafetyCheckError("orders_table_missing")
    source, target = set(_columns(conn, "orders")), set(_canonical_orders_columns())
    source_only = source - target
    unexpected_columns = sorted(source_only - RETIRED_ORDER_COLUMNS)
    if unexpected_columns:
        raise SafetyCheckError("unexpected_noncanonical_orders_columns:" + ",".join(unexpected_columns))
    canonical_objects, canonical_auto = _canonical_objects()
    unexpected_objects = {name for kind, name, sql in _orders_objects(conn) if (kind, name, sql) not in set(canonical_objects)}
    remaining = [signature for _, signature in canonical_auto]
    for name, signature in _auto_indexes(conn):
        if signature in remaining:
            remaining.remove(signature)
        else:
            unexpected_objects.add(name)
    if unexpected_objects:
        raise SafetyCheckError("unexpected_orders_schema_objects:" + ",".join(sorted(unexpected_objects)))
    return {
        "source_only_columns": sorted(source_only),
        "retired_order_columns": sorted(source_only & RETIRED_ORDER_COLUMNS),
        "retained_columns": _canonical_orders_columns(),
        "orders_objects": [{"type": kind, "name": name} for kind, name, _ in _orders_objects(conn)],
        "orders_auto_indexes": [
            {"name": name, "signature": [signature[0], signature[1], signature[2], list(signature[3])]}
            for name, signature in _auto_indexes(conn)
        ],
    }


def _retired_tables_contract(conn: sqlite3.Connection) -> list[str]:
    tables = _table_names(conn)
    unknown = sorted(name for name in tables if name.startswith("h74_") and name not in RETIRED_TABLES)
    if unknown:
        raise SafetyCheckError("unexpected_h74_prefixed_tables:" + ",".join(unknown))
    retired = sorted(tables & RETIRED_TABLES)
    for table in retired:
        columns, contract = set(_columns(conn, table)), RETIRED_TABLE_CONTRACTS[table]
        missing, unexpected = sorted(contract["required"] - columns), sorted(columns - contract["allowed"])
        if missing or unexpected:
            raise SafetyCheckError(f"retired_table_schema_mismatch:{table}:missing={','.join(missing)}:unexpected={','.join(unexpected)}")
    return retired


def _protected_inventory(conn: sqlite3.Connection, retained_orders_columns: list[str]) -> dict[str, object]:
    missing = sorted(set(PROTECTED_TABLES) - _table_names(conn))
    if missing:
        raise SafetyCheckError("protected_table_missing:" + ",".join(missing))
    orders_dependents = orders_foreign_key_inventory(conn)
    protected_tables = tuple(sorted(set(PROTECTED_TABLES) | {str(item["child_table"]) for item in orders_dependents}))
    return {
        table: {
            "columns": (retained_orders_columns if table == "orders" else _columns(conn, table)),
            "row_count": _count(conn, f"SELECT COUNT(*) FROM {_quote(table)}"),
            "row_hash": table_row_hash(conn, table, columns=(retained_orders_columns if table == "orders" else None)),
            "foreign_keys": [dict(item) for item in _foreign_key_rows(conn, table)],
        }
        for table in protected_tables
    }


def _virtual_rows(conn: sqlite3.Connection, pair: str) -> tuple[dict[str, object], ...]:
    table = "strategy_virtual_target_state"
    if table not in _table_names(conn):
        raise SafetyCheckError("strategy_virtual_target_state_missing")
    columns, keys = _columns(conn, table), _primary_key_columns(conn, table)
    if not keys:
        raise SafetyCheckError("retired_virtual_state_primary_key_missing")
    selected, order_by = ", ".join(_quote(column) for column in columns), ", ".join(_quote(column) for column in keys)
    rows = conn.execute(
        f"SELECT {selected} FROM {_quote(table)} WHERE pair=? AND (strategy_name='daily_participation_sma' OR strategy_instance_id LIKE 'daily_participation_sma:%' OR strategy_instance_id LIKE 'h74%') ORDER BY {order_by}",
        (pair,),
    )
    key_indexes = [columns.index(key) for key in keys]
    return tuple(
        {
            "primary_key": {key: row[index] for key, index in zip(keys, key_indexes, strict=True)},
            "row_hash": _row_hash(row, columns),
        }
        for row in rows
    )


def _target_hash(conn: sqlite3.Connection, pair: str) -> str | None:
    table = "target_position_state"
    if table not in _table_names(conn):
        raise SafetyCheckError("target_position_state_missing")
    columns = _columns(conn, table)
    row = conn.execute(f"SELECT {', '.join(_quote(column) for column in columns)} FROM {_quote(table)} WHERE pair=?", (pair,)).fetchone()
    return None if row is None else _row_hash(row, columns)


def _safety_snapshot(
    conn: sqlite3.Connection, pair: str, db_path: Path, manager: PathManager, *, allow_current_lock_owner: bool
) -> dict[str, object]:
    required = {"orders", "portfolio", "open_position_lots"}
    missing = sorted(required - _table_names(conn))
    if missing:
        raise SafetyCheckError("runtime_safety_table_missing:" + ",".join(missing))
    statuses, placeholders = sorted(UNSAFE_ORDER_STATUSES), ",".join("?" for _ in UNSAFE_ORDER_STATUSES)
    portfolio = conn.execute("SELECT asset_qty FROM portfolio WHERE id=1").fetchone()
    lock_status = read_run_lock_status(manager.run_lock_path_for_mode(manager.config.mode))
    lock_available = lock_status.owner_pid is None or (allow_current_lock_owner and lock_status.owner_pid == os.getpid())
    return {
        "active_process_holders": _holders(db_path),
        "risky_order_count": _count(conn, f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})", statuses),
        "pair_risky_order_count": _count(conn, f"SELECT COUNT(*) FROM orders WHERE pair=? AND status IN ({placeholders})", (pair, *statuses)),
        "portfolio_position_present": portfolio is not None,
        "portfolio_asset_qty": float(portfolio[0] or 0.0) if portfolio else 0.0,
        "open_executable_lot_count": _count(conn, "SELECT COALESCE(SUM(executable_lot_count),0) FROM open_position_lots WHERE pair=? AND position_state='open_exposure'", (pair,)),
        "run_lock_diagnostic": {"available": lock_available},
    }


def _blockers(snapshot: dict[str, object], backup_path: Path, action: str, target_present: bool) -> list[str]:
    result: list[str] = []
    if snapshot["active_process_holders"]:
        result.append("database_has_active_process_holder")
    if not snapshot["portfolio_position_present"]:
        result.append("portfolio_position_missing")
    if int(snapshot["risky_order_count"]):
        result.append("unresolved_open_order_count")
    diagnostic = snapshot["run_lock_diagnostic"]
    if isinstance(diagnostic, dict) and not diagnostic.get("available", False):
        result.append("migration_run_lock_unavailable")
    if backup_path.exists():
        result.append("backup_path_already_exists")
    if target_present and action == "clear":
        if abs(float(snapshot["portfolio_asset_qty"])) > FLAT_TOLERANCE:
            result.append("pair_target_state_requires_flat_portfolio")
        if int(snapshot["open_executable_lot_count"]):
            result.append("pair_target_state_requires_zero_open_executable_lots")
        if int(snapshot["pair_risky_order_count"]):
            result.append("pair_target_state_requires_zero_risky_orders")
    return result


def build_plan(
    *, mode: str, pair: str, db_path: Path, backup_path: Path, target_state_action: str | None,
    _allow_current_lock_owner: bool = False,
) -> RetirementPlan:
    pair = str(pair).strip().upper()
    if not pair:
        raise SafetyCheckError("pair_required")
    action = str(target_state_action or "").strip().lower()
    if action and action not in {"retain", "clear"}:
        raise SafetyCheckError("invalid_target_state_action")
    db_path, backup_path, manager = validate_paths(mode=mode, db_path=db_path, backup_path=backup_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _require_integrity(conn, "source_database")
        orders = _orders_contract(conn)
        retired_tables = _retired_tables_contract(conn)
        target_hash = _target_hash(conn, pair)
        virtual = _virtual_rows(conn, pair)
        snapshot = _safety_snapshot(conn, pair, db_path, manager, allow_current_lock_owner=_allow_current_lock_owner)
        blockers = _blockers(snapshot, backup_path, action, target_hash is not None)
        retired_columns = tuple(orders["retired_order_columns"])
        protected = _protected_inventory(conn, list(orders["retained_columns"]))
        schema_inventory = {
            "orders": orders,
            "orders_foreign_key_dependents": [dict(item) for item in orders_foreign_key_inventory(conn)],
            "retired_tables": {table: {"columns": _columns(conn, table), "row_count": _count(conn, f"SELECT COUNT(*) FROM {_quote(table)}")} for table in retired_tables},
        }
        needs_work = bool(retired_columns or retired_tables or virtual or (target_hash is not None and action == "clear"))
        if blockers:
            status = "blocked"
        elif target_hash is not None and not action:
            status = "operator_decision_required"
        elif not needs_work and target_hash is None:
            status = "already_clean"
        else:
            status = "ready"
        actions: list[dict[str, object]] = []
        if virtual:
            actions.append({"action": "delete_retired_virtual_target_state", "count": len(virtual)})
        if target_hash is not None:
            actions.append({"action": "delete_pair_target_state" if action == "clear" else "retain_pair_target_state", "pair": pair})
        if retired_columns:
            actions.append({"action": "rebuild_orders", "remove_columns": list(retired_columns)})
        if retired_tables:
            actions.append({"action": "drop_retired_tables", "tables": retired_tables})
        plan = RetirementPlan(
            schema_version=PLAN_SCHEMA_VERSION, tool_version=TOOL_VERSION, mode=mode, pair=pair,
            db_path=str(db_path), backup_path=str(backup_path), source_db_sha256=sha256_file(db_path),
            target_state_action=action, retired_order_columns=retired_columns, retired_tables=tuple(retired_tables),
            retired_virtual_state_keys=virtual, pair_target_state_present=target_hash is not None,
            pair_target_state_hash=target_hash, protected_inventory=protected, safety_snapshot=snapshot,
            schema_inventory=schema_inventory, actions=tuple(actions), blockers=tuple(blockers), warnings=tuple(),
            status=status, plan_hash="",
        )
        return replace(plan, plan_hash=compute_plan_hash(plan))
    finally:
        conn.close()


def _rebuild_orders(conn: sqlite3.Connection, expected_columns: tuple[str, ...]) -> None:
    current = set(_columns(conn, "orders"))
    if not expected_columns:
        return
    canonical_sql, objects = _canonical_orders_sql()
    temporary = "orders__removed_strategy_retirement_tmp"
    target_sql = canonical_sql.replace("CREATE TABLE IF NOT EXISTS orders", f"CREATE TABLE {temporary}", 1)
    if target_sql == canonical_sql:
        target_sql = canonical_sql.replace("CREATE TABLE orders", f"CREATE TABLE {temporary}", 1)
    if target_sql == canonical_sql:
        raise SafetyCheckError("canonical_orders_schema_unrecognized")
    target = sqlite3.connect(":memory:")
    try:
        target.execute(target_sql)
        retained = _columns(target, temporary)
    finally:
        target.close()
    retained = [column for column in retained if column in current]
    before = table_row_hash(conn, "orders", columns=retained)
    conn.execute(target_sql)
    names = ", ".join(_quote(column) for column in retained)
    conn.execute(f"INSERT INTO {_quote(temporary)} ({names}) SELECT {names} FROM orders")
    if table_row_hash(conn, temporary, columns=retained) != before:
        raise SafetyCheckError("orders_copy_retained_data_mismatch")
    conn.execute("DROP TABLE orders")
    conn.execute(f"ALTER TABLE {_quote(temporary)} RENAME TO orders")
    for _, _, sql in objects:
        conn.execute(sql)
    expected_objects, expected_auto = _canonical_objects()
    if _orders_objects(conn) != expected_objects or _auto_indexes(conn) != expected_auto:
        raise SafetyCheckError("canonical_orders_schema_objects_not_restored")
    if table_row_hash(conn, "orders", columns=retained) != before:
        raise SafetyCheckError("orders_rebuild_retained_data_mismatch")


def _verify_virtual_rows(conn: sqlite3.Connection, plan: RetirementPlan) -> None:
    actual = _virtual_rows(conn, plan.pair)
    if actual != plan.retired_virtual_state_keys:
        raise SafetyCheckError("retirement_plan_stale")


def _stale_fields(reviewed: RetirementPlan, current: RetirementPlan) -> list[str]:
    fields = ("source_db_sha256", "schema_inventory", "protected_inventory", "safety_snapshot", "retired_virtual_state_keys", "pair_target_state_hash", "retired_order_columns", "retired_tables", "backup_path")
    return [field for field in fields if getattr(reviewed, field) != getattr(current, field)]


def _apply_checkpoint(stage: str) -> None:
    """Internal test seam; production calls intentionally have no side effects."""


def _restore_foreign_key_setting(source: sqlite3.Connection, progress: ApplyProgress) -> None:
    if not progress.foreign_keys_disabled:
        return
    try:
        source.execute("PRAGMA foreign_keys=ON")
        if source.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SafetyCheckError("foreign_keys_reenable_failed")
    except Exception:
        progress.foreign_keys_reenabled = False
        raise
    progress.foreign_keys_reenabled = True
    progress.phase = "foreign_keys_reenabled"


def _verify_rollback_state(source: sqlite3.Connection, plan: RetirementPlan) -> None:
    if "orders__removed_strategy_retirement_tmp" in _table_names(source):
        raise SafetyCheckError("rollback_temporary_orders_table_present")
    if not set(plan.retired_order_columns).issubset(_columns(source, "orders")):
        raise SafetyCheckError("rollback_retired_orders_columns_missing")
    if not set(plan.retired_tables).issubset(_table_names(source)):
        raise SafetyCheckError("rollback_retired_tables_missing")
    if _virtual_rows(source, plan.pair) != plan.retired_virtual_state_keys:
        raise SafetyCheckError("rollback_retired_virtual_state_mismatch")
    if _target_hash(source, plan.pair) != plan.pair_target_state_hash:
        raise SafetyCheckError("rollback_pair_target_state_mismatch")
    orders = _orders_contract(source)
    if _protected_inventory(source, list(orders["retained_columns"])) != plan.protected_inventory:
        raise SafetyCheckError("rollback_protected_ledger_contract_mismatch")
    _require_integrity(source, "rollback")


def _attempt_rollback(source: sqlite3.Connection, plan: RetirementPlan, progress: ApplyProgress) -> None:
    progress.phase = "transaction_rolling_back"
    progress.rollback_attempted = True
    try:
        source.rollback()
        if source.in_transaction:
            raise SafetyCheckError("rollback_transaction_still_active")
    except Exception:
        progress.rollback_succeeded = False
        return
    try:
        _restore_foreign_key_setting(source, progress)
        _verify_rollback_state(source, plan)
    except Exception:
        progress.rollback_succeeded = False
        return
    progress.rollback_succeeded = True
    progress.commit_outcome = "rolled_back"
    progress.phase = "transaction_rolled_back"


def _reason_code(exc: Exception) -> str:
    code, _, _ = str(exc).partition(":")
    return code or type(exc).__name__


def _record_backup_state(progress: ApplyProgress) -> None:
    if progress.backup_created or not progress.backup_path:
        return
    path = Path(progress.backup_path)
    if not path.exists():
        return
    progress.backup_created = True
    progress.phase = "backup_created"
    if path.is_file() and path.stat().st_size > 0:
        try:
            progress.backup_sha256 = sha256_file(path)
        except OSError:
            pass


def _classify_apply_failure(exc: Exception, *, progress: ApplyProgress) -> RetirementApplyError | None:
    _record_backup_state(progress)
    if not progress.backup_created and not progress.transaction_started and not progress.commit_started:
        return None
    changed_fields: tuple[str, ...] = ()
    if _reason_code(exc) == "retirement_plan_stale":
        _, _, details = str(exc).partition(":")
        changed_fields = tuple(sorted(item for item in details.split(",") if item))
    return RetirementApplyError(
        _reason_code(exc), progress=progress, changed_fields=changed_fields, original_error=exc,
    )


def _apply_plan_inner(
    *, plan_path: Path, expected_plan_hash: str, confirmation: str, broker_local_converged: bool,
    progress: ApplyProgress,
) -> dict[str, object]:
    plan = load_plan(plan_path)
    if expected_plan_hash != plan.plan_hash:
        raise SafetyCheckError("retirement_plan_hash_mismatch")
    if plan.status != "ready":
        raise SafetyCheckError("retirement_plan_not_ready")
    if confirmation != CONFIRMATION:
        raise SafetyCheckError("explicit_confirmation_required")
    if not broker_local_converged:
        raise SafetyCheckError("broker_local_position_convergence_operator_attestation_required")
    db_path, backup_path, manager = validate_paths(mode=plan.mode, db_path=Path(plan.db_path), backup_path=Path(plan.backup_path))
    progress.backup_path = str(backup_path)
    try:
        with acquire_run_lock(manager.run_lock_path_for_mode(plan.mode)):
            current = build_plan(
                mode=plan.mode, pair=plan.pair, db_path=db_path, backup_path=backup_path,
                target_state_action=plan.target_state_action, _allow_current_lock_owner=True,
            )
            stale = _stale_fields(plan, current)
            if stale or current.plan_hash != plan.plan_hash:
                raise SafetyCheckError("retirement_plan_stale:" + ",".join(stale or ["plan_hash"]))
            if backup_path.exists():
                raise SafetyCheckError("retirement_plan_stale:backup_path_availability")
            if sha256_file(db_path) != plan.source_db_sha256:
                raise SafetyCheckError("retirement_plan_stale:source_db_sha256")
            source = sqlite3.connect(db_path)
            try:
                source.execute("PRAGMA foreign_keys=ON")
                _require_integrity(source, "source_database")
                manager.ensure_parent_dir(backup_path)
                progress.phase = "backup_creating"
                with sqlite3.connect(backup_path) as backup:
                    source.backup(backup)
                progress.backup_created = True
                progress.phase = "backup_created"
                _apply_checkpoint("after_backup_created")
                backup_sha = sha256_file(backup_path)
                progress.backup_sha256 = backup_sha
                verify_backup(plan=plan, backup_path=backup_path, expected_sha256=backup_sha)
                progress.backup_verified = True
                progress.phase = "backup_verified"
                _apply_checkpoint("after_backup_verified")
                if source.in_transaction:
                    raise SafetyCheckError("unexpected_active_transaction_before_rebuild")
                try:
                    source.execute("PRAGMA foreign_keys=OFF")
                    progress.foreign_keys_disabled = True
                    if source.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                        raise SafetyCheckError("foreign_keys_disable_failed")
                    source.execute("BEGIN IMMEDIATE")
                    progress.transaction_started = True
                    progress.phase = "transaction_started"
                    _apply_checkpoint("after_transaction_started")
                    _verify_virtual_rows(source, plan)
                    source.execute("DELETE FROM strategy_virtual_target_state WHERE pair=? AND (strategy_name='daily_participation_sma' OR strategy_instance_id LIKE 'daily_participation_sma:%' OR strategy_instance_id LIKE 'h74%')", (plan.pair,))
                    if plan.target_state_action == "clear" and plan.pair_target_state_present:
                        source.execute("DELETE FROM target_position_state WHERE pair=?", (plan.pair,))
                    _rebuild_orders(source, plan.retired_order_columns)
                    _apply_checkpoint("after_orders_rebuilt")
                    for table in plan.retired_tables:
                        source.execute(f"DROP TABLE {_quote(table)}")
                    canonical_columns = _canonical_orders_columns()
                    if _protected_inventory(source, canonical_columns) != plan.protected_inventory:
                        raise SafetyCheckError("protected_ledger_contract_mismatch")
                    if source.execute("PRAGMA foreign_key_check").fetchall():
                        raise SafetyCheckError("post_rebuild_foreign_key_check_failed")
                    if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise SafetyCheckError("post_rebuild_integrity_check_failed")
                    _apply_checkpoint("before_commit")
                    progress.commit_started = True
                    progress.phase = "commit_started"
                    progress.commit_outcome = "unknown"
                    source.commit()
                    progress.transaction_committed = True
                    progress.commit_outcome = "committed"
                    progress.phase = "committed"
                    _apply_checkpoint("after_commit")
                except Exception:
                    if progress.transaction_started and not progress.commit_started:
                        _attempt_rollback(source, plan, progress)
                    elif progress.commit_started:
                        # A commit exception can mean either outcome.  Restore and
                        # record this connection-local guard when SQLite permits it,
                        # but never treat that attempt as proof of commit outcome.
                        try:
                            _restore_foreign_key_setting(source, progress)
                        except Exception:
                            pass
                    elif progress.foreign_keys_disabled:
                        try:
                            _restore_foreign_key_setting(source, progress)
                        except Exception:
                            pass
                    raise
                _restore_foreign_key_setting(source, progress)
                _apply_checkpoint("before_post_commit_verification")
                _require_integrity(source, "post_migration")
                progress.post_commit_verified = True
                progress.phase = "post_commit_verified"
            finally:
                source.close()
    except RunLockError as exc:
        raise SafetyCheckError("migration_run_lock_unavailable") from exc
    status = "applied_with_retained_target_state" if plan.pair_target_state_present and plan.target_state_action == "retain" else "applied"
    database_modified = bool(
        plan.retired_order_columns
        or plan.retired_tables
        or plan.retired_virtual_state_keys
        or (plan.target_state_action == "clear" and plan.pair_target_state_present)
    )
    return {
        "status": status, "plan_hash": plan.plan_hash, "phase": progress.phase,
        "backup_path": str(backup_path), "backup_sha256": backup_sha,
        "backup_created": progress.backup_created, "backup_verified": progress.backup_verified,
        "database_modified": database_modified, "transaction_started": progress.transaction_started,
        "transaction_committed": progress.transaction_committed,
        "commit_outcome": progress.commit_outcome, "rollback_attempted": progress.rollback_attempted,
        "rollback_succeeded": progress.rollback_succeeded,
        "foreign_keys_reenabled": progress.foreign_keys_reenabled,
        "post_commit_verified": progress.post_commit_verified, "recovery_required": False,
        "recommended_action": "none",
        "pair_target_state_action": plan.target_state_action,
        "pair_target_state_retained_by_operator_decision": plan.pair_target_state_present and plan.target_state_action == "retain",
    }


def apply_plan(*, plan_path: Path, expected_plan_hash: str, confirmation: str, broker_local_converged: bool) -> dict[str, object]:
    progress = ApplyProgress()
    try:
        return _apply_plan_inner(
            plan_path=plan_path, expected_plan_hash=expected_plan_hash, confirmation=confirmation,
            broker_local_converged=broker_local_converged, progress=progress,
        )
    except RetirementApplyError:
        raise
    except RunLockError as exc:
        raise SafetyCheckError("migration_run_lock_unavailable") from exc
    except Exception as exc:
        classified = _classify_apply_failure(exc, progress=progress)
        if classified is not None:
            raise classified from exc
        raise


def verify_backup(*, plan: RetirementPlan, backup_path: Path, expected_sha256: str) -> dict[str, object]:
    backup_path = backup_path.expanduser().resolve()
    if backup_path != Path(plan.backup_path).expanduser().resolve():
        raise SafetyCheckError("backup_path_plan_mismatch")
    validate_paths(mode=plan.mode, db_path=Path(plan.db_path), backup_path=backup_path)
    if not backup_path.is_file() or backup_path.stat().st_size <= 0:
        raise SafetyCheckError("backup_missing_or_empty")
    if not expected_sha256:
        raise SafetyCheckError("backup_expected_sha256_required")
    digest = sha256_file(backup_path)
    if digest != expected_sha256:
        raise SafetyCheckError("backup_sha256_mismatch")
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        _require_integrity(conn, "backup")
        orders = _orders_contract(conn)
        retired_tables = _retired_tables_contract(conn)
        protected = _protected_inventory(conn, list(orders["retained_columns"]))
        if protected != plan.protected_inventory:
            raise SafetyCheckError("backup_protected_ledger_contract_mismatch")
        if tuple(orders["retired_order_columns"]) != plan.retired_order_columns or tuple(retired_tables) != plan.retired_tables:
            raise SafetyCheckError("backup_retired_schema_contract_mismatch")
        if _virtual_rows(conn, plan.pair) != plan.retired_virtual_state_keys or _target_hash(conn, plan.pair) != plan.pair_target_state_hash:
            raise SafetyCheckError("backup_runtime_state_contract_mismatch")
    finally:
        conn.close()
    return {"status": "backup_verified", "plan_hash": plan.plan_hash, "backup_sha256": digest, "database_modified": False}
