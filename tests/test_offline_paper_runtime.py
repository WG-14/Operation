from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from operation.db_core import ensure_db
from operation.operation_strategy.registry import resolve_operation_strategy_plugin


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "runtime"
    return {
        **os.environ,
        "MODE": "paper",
        "ENV_ROOT": str(root / "env"),
        "RUN_ROOT": str(root / "run"),
        "DATA_ROOT": str(root / "data"),
        "LOG_ROOT": str(root / "logs"),
        "BACKUP_ROOT": str(root / "backup"),
        "ARCHIVE_ROOT": str(root / "archive"),
        "DB_PATH": str(root / "data" / "paper" / "trades" / "paper.sqlite"),
        "NOTIFIER_ENABLED": "false",
        "LIVE_DRY_RUN": "false",
        "LIVE_REAL_ORDER_ARMED": "false",
        "STRATEGY_NAME": "sma_with_filter",
        "PAIR": "KRW-BTC",
        "MARKET": "KRW-BTC",
        "INTERVAL": "1m",
        "SMA_SHORT": "7",
        "SMA_LONG": "30",
    }


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "operation", "run"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_empty_paper_database_runs_one_offline_no_data_cycle(tmp_path: Path) -> None:
    result = _run(_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert '"cycle_id": "skip:no_candles"' in result.stdout


def test_seeded_paper_database_persists_a_local_strategy_decision(tmp_path: Path) -> None:
    env = _env(tmp_path)
    db_path = env["DB_PATH"]
    conn = ensure_db(db_path)
    try:
        now_ms = int(time.time() * 1000)
        minute_ms = 60_000
        latest_closed_bucket = ((now_ms // minute_ms) - 1) * minute_ms
        plugin = resolve_operation_strategy_plugin("sma_with_filter")
        requirements = plugin.runtime_data_requirement_builder(  # type: ignore[misc]
            SimpleNamespace(parameters={"SMA_SHORT": 7, "SMA_LONG": 30})
        )
        required_rows = next(
            item.lookback_rows for item in requirements.capabilities if item.name == "candles"
        )
        seeded_rows = int(required_rows) + 4
        seeded_timestamps: list[int] = []
        for index in range(seeded_rows):
            ts = latest_closed_bucket - (seeded_rows - 1 - index) * minute_ms
            seeded_timestamps.append(ts)
            close = 100.0 + index * 0.1
            conn.execute(
                "INSERT INTO candles(ts,pair,interval,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                (ts, "KRW-BTC", "1m", close, close + 1, close - 1, close, 1.0),
            )
        conn.commit()
    finally:
        conn.close()

    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "skip:no_candles" not in result.stdout
    assert "skip:insufficient_signal_history" not in result.stdout
    assert "LIVE_BROKER_NOT_CONFIGURED" not in result.stdout
    assert "decision_persistence_failed" not in result.stdout
    assert '"cycle_id": "checkpoint:processed"' in result.stdout

    conn = ensure_db(db_path)
    try:
        expected_counts = {
            "runtime_strategy_set_manifest": 1,
            "runtime_dependency_manifest": 1,
            "runtime_strategy_decision_bundle": 1,
            "runtime_strategy_decision_result": 1,
            "strategy_decisions": 1,
        }
        for table, minimum in expected_counts.items():
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count >= minimum, f"{table} count={count}"
        row = conn.execute(
            """
            SELECT strategy_name, signal, reason, candle_ts, context_json
            FROM strategy_decisions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["strategy_name"] == "sma_with_filter"
    assert row["signal"] in {"BUY", "SELL", "HOLD"}
    assert row["reason"]
    assert int(row["candle_ts"]) in seeded_timestamps
    context = json.loads(row["context_json"])
    assert context["runtime_decision_request_hash"].startswith("sha256:")
    assert context["feature_snapshot_hash"].startswith("sha256:")
    assert context["runtime_strategy_decision_bundle_hash"].startswith("sha256:")
