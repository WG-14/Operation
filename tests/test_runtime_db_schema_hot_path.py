from __future__ import annotations

from pathlib import Path

from bithumb_bot.db_core import ensure_db


def test_runtime_container_uses_schema_ready_db_factory_after_startup() -> None:
    source = Path("src/bithumb_bot/runtime/app_container.py").read_text(encoding="utf-8")
    assert "startup_schema_conn = ensure_db(ensure_schema_ready=True)" in source
    assert "return ensure_db(ensure_schema_ready=False)" in source
    assert "db_factory=ensure_db" not in source


def test_ensure_schema_ready_false_still_applies_pragmas(tmp_path) -> None:
    db_path = tmp_path / "schema-ready-false.sqlite"
    conn = ensure_db(str(db_path), ensure_schema_ready=False)
    try:
        foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    finally:
        conn.close()

    assert foreign_keys == 1
    assert busy_timeout >= 0
