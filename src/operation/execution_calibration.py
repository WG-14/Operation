from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_hashing import sha256_prefixed
from .paths import PathManager, PathPolicyError
from .storage_io import write_json_atomic


def build_calibration_artifact(
    *,
    summary: dict[str, object],
    market: str,
    interval: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the operation-owned execution-quality calibration report payload."""
    sample_count = int(summary.get("sample_count") or 0)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "execution_cost_calibration",
        "market": str(market),
        "interval": str(interval),
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "sample_count": sample_count,
        "p50_slippage_bps": summary.get("median_slippage_vs_signal_bps"),
        "p90_slippage_bps": summary.get("p90_slippage_vs_signal_bps"),
        "p95_slippage_bps": summary.get("p95_slippage_vs_signal_bps"),
        "p95_full_fill_latency_ms": summary.get("p95_submit_to_fill_ms"),
        "partial_fill_rate": summary.get("partial_fill_rate"),
        "unfilled_rate": summary.get("unfilled_rate"),
        "model_breach_rate": summary.get("model_breach_rate"),
        "quality_gate_status": summary.get("quality_gate_status"),
        "primary_issue": summary.get("primary_issue"),
        "signal_reference_price_source": summary.get("signal_reference_price_source") or "signal_context",
        "submit_reference_price_source": summary.get("submit_reference_price_source") or "submit_context",
        "fill_price_source": summary.get("fill_price_source") or "recorded_fill_avg_price",
        "backtest_fill_reference_policy": summary.get("backtest_fill_reference_policy"),
        "execution_reality_level": summary.get("execution_reality_level"),
        "execution_reality_contract": summary.get("execution_reality_contract"),
        "execution_contract_hash": summary.get("execution_contract_hash"),
        "execution_contract_hashes": list(summary.get("execution_contract_hashes") or []),
        "execution_contract_hash_present": bool(summary.get("execution_contract_hash_present")),
        "mixed_execution_contract_hashes": bool(summary.get("mixed_execution_contract_hashes")),
        "execution_contract_mismatch_count": int(summary.get("execution_contract_mismatch_count") or 0),
        "execution_contract_missing_count": int(summary.get("execution_contract_missing_count") or 0),
        "insufficient_evidence": sample_count <= 0 or summary.get("quality_gate_status") == "INSUFFICIENT_EVIDENCE",
        "recommended_research_cost_model": _recommended_model(summary),
    }
    payload["content_hash"] = sha256_prefixed({key: value for key, value in payload.items() if key != "content_hash"})
    return payload


def write_calibration_artifact(*, manager: PathManager, artifact: dict[str, Any]) -> Path:
    """Write the diagnostic-only calibration report to the managed reports bucket."""
    market = str(artifact.get("market") or "unknown").replace("/", "_").replace(":", "_")
    stamp = str(artifact.get("generated_at") or datetime.now(timezone.utc).isoformat())
    safe_stamp = "".join(ch if ch.isdigit() else "_" for ch in stamp)[:14]
    path = manager.data_dir() / "reports" / "execution_quality" / f"cost_model_calibration_{market}_{safe_stamp}.json"
    if PathManager._is_within(path.resolve(), manager.project_root.resolve()):
        raise PathPolicyError(f"execution calibration output path must be outside repository: {path.resolve()}")
    write_json_atomic(path, artifact)
    return path


def _recommended_model(summary: dict[str, object]) -> dict[str, object]:
    p90 = _float_or_none(summary.get("p90_slippage_vs_signal_bps"))
    p95 = _float_or_none(summary.get("p95_slippage_vs_signal_bps"))
    latency = _float_or_none(summary.get("p95_submit_to_fill_ms"))
    partial = _float_or_none(summary.get("partial_fill_rate")) or 0.0
    unfilled = _float_or_none(summary.get("unfilled_rate")) or 0.0
    return {
        "slippage_bps": sorted({10.0, round(max(0.0, p90 or 0.0), 2), round(max(0.0, p95 or 0.0), 2)}),
        "latency_ms": sorted({500, 1500, int(max(3000.0, latency or 0.0))}),
        "partial_fill_rate": sorted({0.0, round(max(0.0, partial), 4)}),
        "order_failure_rate": sorted({0.0, round(max(0.0, unfilled), 4)}),
    }


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed
