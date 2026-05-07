from __future__ import annotations

import pytest

from bithumb_bot.research.execution_calibration import (
    ExecutionCalibrationError,
    build_calibration_artifact,
    compare_calibration_to_scenario,
    validate_calibration_artifact,
)


def test_calibration_artifact_schema_is_hash_validated() -> None:
    artifact = build_calibration_artifact(
        summary={
            "sample_count": 40,
            "median_slippage_vs_signal_bps": 4.0,
            "p90_slippage_vs_signal_bps": 12.0,
            "p95_slippage_vs_signal_bps": 18.0,
            "p95_submit_to_fill_ms": 1500,
            "partial_fill_rate": 0.02,
            "unfilled_rate": 0.01,
            "model_breach_rate": 0.03,
            "quality_gate_status": "PASS",
        },
        market="KRW-BTC",
        interval="1m",
        generated_at="2026-05-03T00:00:00+00:00",
    )

    validated = validate_calibration_artifact(artifact)

    assert validated["artifact_type"] == "execution_cost_calibration"
    assert validated["content_hash"].startswith("sha256:")
    assert validated["recommended_research_cost_model"]["slippage_bps"]


def test_calibration_hash_mismatch_is_rejected() -> None:
    artifact = build_calibration_artifact(
        summary={"sample_count": 1, "quality_gate_status": "PASS"},
        market="KRW-BTC",
        interval="1m",
    )
    artifact["sample_count"] = 2

    with pytest.raises(ExecutionCalibrationError, match="content_hash_mismatch"):
        validate_calibration_artifact(artifact)


def test_calibration_comparison_fails_when_observed_costs_exceed_assumptions() -> None:
    artifact = build_calibration_artifact(
        summary={
            "sample_count": 40,
            "p90_slippage_vs_signal_bps": 12.0,
            "p95_slippage_vs_signal_bps": 18.0,
            "p95_submit_to_fill_ms": 2500,
            "model_breach_rate": 0.0,
            "quality_gate_status": "PASS",
        },
        market="KRW-BTC",
        interval="1m",
    )

    result = compare_calibration_to_scenario(
        calibration=artifact,
        assumed_slippage_bps=10.0,
        assumed_latency_ms=3000,
    )

    assert result["status"] == "FAIL"
    assert "execution_calibration_p90_slippage_exceeds_assumption" in result["reasons"]


def test_calibration_comparison_fails_on_market_and_interval_mismatch() -> None:
    artifact = build_calibration_artifact(
        summary={"sample_count": 40, "quality_gate_status": "PASS"},
        market="KRW-ETH",
        interval="5m",
    )

    result = compare_calibration_to_scenario(
        calibration=artifact,
        assumed_slippage_bps=10.0,
        assumed_latency_ms=3000,
        expected_market="KRW-BTC",
        expected_interval="1m",
    )

    assert result["status"] == "FAIL"
    assert "execution_calibration_market_mismatch" in result["reasons"]
    assert "execution_calibration_interval_mismatch" in result["reasons"]


def test_required_calibration_without_content_hash_fails_closed() -> None:
    artifact = build_calibration_artifact(
        summary={"sample_count": 40, "quality_gate_status": "PASS"},
        market="KRW-BTC",
        interval="1m",
    )
    artifact.pop("content_hash")

    result = compare_calibration_to_scenario(
        calibration=artifact,
        assumed_slippage_bps=10.0,
        assumed_latency_ms=3000,
        expected_market="KRW-BTC",
        expected_interval="1m",
        require_content_hash=True,
    )

    assert result["status"] == "FAIL"
    assert "execution_calibration_content_hash_missing" in result["reasons"]


def _artifact(**overrides):
    summary = {
        "sample_count": 50,
        "median_slippage_vs_signal_bps": 1.0,
        "p90_slippage_vs_signal_bps": 2.0,
        "p95_slippage_vs_signal_bps": 3.0,
        "p95_submit_to_fill_ms": 100,
        "partial_fill_rate": 0.0,
        "unfilled_rate": 0.0,
        "model_breach_rate": 0.0,
        "quality_gate_status": "PASS",
    }
    summary.update(overrides)
    return build_calibration_artifact(
        summary=summary,
        market="KRW-BTC",
        interval="1m",
        generated_at="2026-05-07T00:00:00+00:00",
    )


def _compare(calibration, **overrides):
    kwargs = {
        "calibration": calibration,
        "assumed_slippage_bps": 5.0,
        "assumed_latency_ms": 200,
        "assumed_partial_fill_rate": 0.0,
        "assumed_order_failure_rate": 0.0,
        "expected_market": "KRW-BTC",
        "expected_interval": "1m",
        "require_content_hash": True,
        "min_sample_count": 30,
        "require_quality_gate_pass": True,
    }
    kwargs.update(overrides)
    return compare_calibration_to_scenario(**kwargs)


def test_calibration_fails_when_partial_fill_rate_exceeds_assumption() -> None:
    result = _compare(_artifact(partial_fill_rate=0.01))

    assert result["status"] == "FAIL"
    assert "execution_calibration_partial_fill_rate_exceeds_assumption" in result["reasons"]


def test_calibration_fails_when_unfilled_rate_exceeds_assumption() -> None:
    result = _compare(_artifact(unfilled_rate=0.02))

    assert result["status"] == "FAIL"
    assert "execution_calibration_unfilled_rate_exceeds_assumption" in result["reasons"]


def test_calibration_fails_when_sample_count_below_required() -> None:
    result = _compare(_artifact(sample_count=29))

    assert result["status"] == "FAIL"
    assert "execution_calibration_sample_count_below_required" in result["reasons"]


def test_calibration_fails_when_quality_gate_did_not_pass() -> None:
    result = _compare(_artifact(quality_gate_status="FAIL"))

    assert result["status"] == "FAIL"
    assert "execution_calibration_quality_gate_not_passed" in result["reasons"]


def test_calibration_passes_when_fill_rates_are_within_assumptions() -> None:
    result = _compare(
        _artifact(partial_fill_rate=0.01, unfilled_rate=0.02),
        assumed_partial_fill_rate=0.02,
        assumed_order_failure_rate=0.03,
    )

    assert result["status"] == "PASS"
    assert result["reasons"] == []
    assert result["observed_partial_fill_rate"] == 0.01
    assert result["observed_unfilled_rate"] == 0.02


def test_optional_warn_mode_missing_calibration_is_explicit() -> None:
    result = _compare(
        None,
        require_content_hash=False,
        min_sample_count=None,
        require_quality_gate_pass=False,
    )

    assert result["status"] == "MISSING"
    assert result["reasons"] == ["execution_calibration_missing"]
