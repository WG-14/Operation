from __future__ import annotations

import hashlib
import json
import subprocess
from functools import lru_cache
from typing import Mapping

from .config import PROJECT_ROOT, settings


@lru_cache(maxsize=1)
def current_code_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def build_experiment_fingerprint_payload(
    *,
    strategy_name: str | None = None,
    parameter_overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    from .runtime_strategy_set import RuntimeDecisionRequestBuilder, RuntimeStrategySpec

    selected_strategy_name = str(strategy_name or settings.STRATEGY_NAME).strip().lower()
    request = RuntimeDecisionRequestBuilder().build_for_spec(
        RuntimeStrategySpec(
            strategy_name=selected_strategy_name,
            parameters=dict(parameter_overrides or {}) or None,
            parameter_source="experiment_fingerprint_override" if parameter_overrides else None,
        ),
        through_ts_ms=None,
    )
    runtime_spec = request.runtime_strategy_spec
    runtime_contract = (
        dict(runtime_spec.runtime_contract)
        if hasattr(runtime_spec, "runtime_contract")
        else {}
    )
    return {
        "schema_version": 2,
        "code_commit_sha": current_code_commit_sha(),
        "strategy_name": request.strategy_name,
        "strategy_version": request.strategy_version,
        "strategy_parameters_hash": request.strategy_parameters_hash,
        "runtime_contract_hash": request.runtime_contract_hash,
        "plugin_contract_hash": request.plugin_contract_hash,
        "execution_contract_hash": runtime_contract.get("execution_contract_hash"),
        "runtime_decision_request_hash": request.request_hash,
        "parameter_source": request.parameter_source,
        "approved_profile_path": request.approved_profile_path,
        "approved_profile_hash": request.approved_profile_hash,
        "execution_engine": str(settings.EXECUTION_ENGINE),
        "target_exposure_krw": settings.TARGET_EXPOSURE_KRW,
        "max_order_krw": float(settings.MAX_ORDER_KRW),
        "fee_rate_estimate": float(settings.LIVE_FEE_RATE_ESTIMATE),
        "fee_authority_ref": "settings.LIVE_FEE_RATE_ESTIMATE",
        "slippage_bps": float(settings.STRATEGY_ENTRY_SLIPPAGE_BPS),
        "market": str(settings.PAIR),
        "interval": str(settings.INTERVAL),
        "max_daily_loss_krw": float(settings.MAX_DAILY_LOSS_KRW),
        "max_position_loss_pct": float(settings.MAX_POSITION_LOSS_PCT),
        "max_daily_order_count": int(settings.MAX_DAILY_ORDER_COUNT),
        "order_sizing_policy_version": "target_delta_exchange_floor_v1",
        "approval_state": {
            "mode": str(settings.MODE),
            "live_dry_run": bool(settings.LIVE_DRY_RUN),
            "live_real_order_armed": bool(settings.LIVE_REAL_ORDER_ARMED),
        },
    }


def experiment_fingerprint(
    *,
    strategy_name: str | None = None,
    parameter_overrides: Mapping[str, object] | None = None,
) -> str:
    payload = build_experiment_fingerprint_payload(
        strategy_name=strategy_name,
        parameter_overrides=parameter_overrides,
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def experiment_context(
    *,
    strategy_name: str | None = None,
    parameter_overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload = build_experiment_fingerprint_payload(
        strategy_name=strategy_name,
        parameter_overrides=parameter_overrides,
    )
    return {
        "experiment_id": experiment_fingerprint(
            strategy_name=strategy_name,
            parameter_overrides=parameter_overrides,
        ),
        "experiment_fingerprint": experiment_fingerprint(
            strategy_name=strategy_name,
            parameter_overrides=parameter_overrides,
        ),
        "experiment_fingerprint_version": "experiment_fingerprint_v2",
        "experiment_fingerprint_inputs": payload,
    }
