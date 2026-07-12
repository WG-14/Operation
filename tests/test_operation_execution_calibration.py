from __future__ import annotations

import json
from pathlib import Path

import pytest

from operation.artifact_hashing import sha256_prefixed
from operation.execution_calibration import build_calibration_artifact, write_calibration_artifact
from operation.paths import PathConfig, PathManager, PathPolicyError


def _manager(*, project_root: Path, data_root: Path) -> PathManager:
    return PathManager(
        project_root=project_root,
        config=PathConfig(
            mode="paper",
            env_root=data_root.parent / "env",
            run_root=data_root.parent / "run",
            data_root=data_root,
            log_root=data_root.parent / "logs",
            backup_root=data_root.parent / "backup",
            archive_root=data_root.parent / "archive",
        ),
    )


def test_operation_calibration_payload_preserves_schema_and_hash() -> None:
    summary = {
        "sample_count": 7,
        "median_slippage_vs_signal_bps": 1.25,
        "p90_slippage_vs_signal_bps": 2.5,
        "p95_slippage_vs_signal_bps": 3.75,
        "p95_submit_to_fill_ms": 1200,
        "partial_fill_rate": 0.01234,
        "unfilled_rate": 0.02,
        "model_breach_rate": 0.03,
        "quality_gate_status": "PASS",
        "primary_issue": "none",
        "execution_contract_hashes": ["sha256:contract"],
        "execution_contract_hash_present": True,
    }

    artifact = build_calibration_artifact(
        summary=summary,
        market="KRW-BTC",
        interval="1m",
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert artifact == {
        "schema_version": 1,
        "artifact_type": "execution_cost_calibration",
        "market": "KRW-BTC",
        "interval": "1m",
        "generated_at": "2026-05-03T00:00:00+00:00",
        "sample_count": 7,
        "p50_slippage_bps": 1.25,
        "p90_slippage_bps": 2.5,
        "p95_slippage_bps": 3.75,
        "p95_full_fill_latency_ms": 1200,
        "partial_fill_rate": 0.01234,
        "unfilled_rate": 0.02,
        "model_breach_rate": 0.03,
        "quality_gate_status": "PASS",
        "primary_issue": "none",
        "signal_reference_price_source": "signal_context",
        "submit_reference_price_source": "submit_context",
        "fill_price_source": "recorded_fill_avg_price",
        "backtest_fill_reference_policy": None,
        "execution_reality_level": None,
        "execution_reality_contract": None,
        "execution_contract_hash": None,
        "execution_contract_hashes": ["sha256:contract"],
        "execution_contract_hash_present": True,
        "mixed_execution_contract_hashes": False,
        "execution_contract_mismatch_count": 0,
        "execution_contract_missing_count": 0,
        "insufficient_evidence": False,
        "recommended_research_cost_model": {
            "slippage_bps": [2.5, 3.75, 10.0],
            "latency_ms": [500, 1500, 3000],
            "partial_fill_rate": [0.0, 0.0123],
            "order_failure_rate": [0.0, 0.02],
        },
        "content_hash": "sha256:39fde40609c0214c4e23803826be99db1b5c254d57deb1939a8b8fe5c725ca35",
    }
    assert artifact["content_hash"] == sha256_prefixed(
        {key: value for key, value in artifact.items() if key != "content_hash"}
    )


def test_operation_calibration_marks_insufficient_evidence_and_preserves_market_interval() -> None:
    artifact = build_calibration_artifact(
        summary={"sample_count": 0, "quality_gate_status": "INSUFFICIENT_EVIDENCE"},
        market="KRW-ETH",
        interval="5m",
        generated_at="2026-05-03T00:00:00+00:00",
    )

    assert artifact["insufficient_evidence"] is True
    assert artifact["market"] == "KRW-ETH"
    assert artifact["interval"] == "5m"
    assert artifact["sample_count"] == 0


def test_operation_calibration_rejects_repo_internal_output_path(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    data_root = project_root / "runtime-data"
    project_root.mkdir()
    artifact = build_calibration_artifact(summary={}, market="KRW-BTC", interval="1m", generated_at="2026-05-03T00:00:00+00:00")

    with pytest.raises(PathPolicyError, match="outside repository"):
        write_calibration_artifact(manager=_manager(project_root=project_root, data_root=data_root), artifact=artifact)


def test_operation_calibration_writes_external_reports_path(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    data_root = tmp_path / "operation-runtime" / "data"
    project_root.mkdir()
    artifact = build_calibration_artifact(summary={}, market="KRW-BTC", interval="1m", generated_at="2026-05-03T00:00:00+00:00")

    path = write_calibration_artifact(manager=_manager(project_root=project_root, data_root=data_root), artifact=artifact)

    assert path == data_root / "paper" / "reports" / "execution_quality" / "cost_model_calibration_KRW-BTC_2026_05_03_00_.json"
    assert json.loads(path.read_text(encoding="utf-8")) == artifact
