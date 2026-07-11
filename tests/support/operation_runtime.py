from __future__ import annotations

from pathlib import Path

import pytest

from bithumb_bot.config import settings
from bithumb_bot.decision_equivalence import sha256_prefixed


def set_live_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_dir: Path,
    db_path: Path | None = None,
) -> None:
    """Configure repository-external, mode-separated paths for live safety tests."""
    roots = {
        "ENV_ROOT": (base_dir / "env").resolve(),
        "RUN_ROOT": (base_dir / "run").resolve(),
        "DATA_ROOT": (base_dir / "data").resolve(),
        "LOG_ROOT": (base_dir / "logs").resolve(),
        "BACKUP_ROOT": (base_dir / "backup").resolve(),
    }
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("RUN_LOCK_PATH", str((roots["RUN_ROOT"] / "live" / "bithumb-bot.lock").resolve()))
    live_db_path = (
        db_path.resolve()
        if db_path is not None
        else (roots["DATA_ROOT"] / "live" / "trades" / "live.sqlite").resolve()
    )
    monkeypatch.setenv("DB_PATH", str(live_db_path))
    object.__setattr__(settings, "DB_PATH", str(live_db_path))


def unit_runtime_strategy_set_manifest(**_kwargs: object) -> dict[str, object]:
    """Minimal Operation-owned manifest for restart and recovery test fixtures."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "authority_label": "RuntimeStrategySetManifest",
        "authority_scope": "operator_reproducibility_manifest",
        "source": "unit",
        "runtime_pair": "KRW-BTC",
        "runtime_interval": "1m",
        "single_pair_runtime_enforced": True,
        "market_scope": {
            "schema_version": 1,
            "mode": "single_pair",
            "pair": "KRW-BTC",
            "interval": "1m",
        },
        "multi_strategy_enabled": False,
        "active_strategy_count": 1,
        "active_strategy_pairs": ["KRW-BTC"],
        "active_strategy_intervals": ["1m"],
        "active_instances": [
            {
                "strategy_instance_id": "unit",
                "strategy_name": "sma_with_filter",
                "parameter_source": "runtime_strategy_spec",
                "legacy_compatibility_used": False,
                "runtime_decision_request_hash": "sha256:unit-request",
                "runtime_decision_request_hash_scope": "run_start_blueprint_through_ts_null",
            }
        ],
        "strategy_instance_profile_bindings": [],
        "execution_config_hash": "sha256:unit-execution",
        "risk_config_hash": "sha256:unit-risk",
    }
    payload["runtime_strategy_set_manifest_hash"] = sha256_prefixed(payload)
    return payload
