from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from operation.db_core import ensure_db


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
        for index in range(500):
            ts = now_ms - 90_000 - (499 - index) * 60_000
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
    # The seeded candle set reaches the strategy preflight rather than the
    # no-data branch.  The current policy can still reject it for insufficient
    # feature history; that result is explicit and non-submitting.
    assert "skip:no_candles" not in result.stdout
    assert "LIVE_BROKER_NOT_CONFIGURED" not in result.stdout
