from __future__ import annotations

from pathlib import Path

from bithumb_bot.research.deployment_policy import validate_production_calibration_policy
from bithumb_bot.research.experiment_manifest import load_manifest


def _candidate(**overrides):
    payload = {
        "deployment_tier": "paper_candidate",
        "execution_model_source": "execution_model",
        "execution_model": {"type": "fixed_bps", "model_params_hash": "sha256:model"},
        "execution_calibration_required": True,
        "execution_calibration_strictness": "fail",
        "execution_calibration_gate": {
            "status": "PASS",
            "reasons": [],
            "artifact_hash": "sha256:calibration",
            "artifact_hashes": ["sha256:calibration"],
            "scenario_gates": [
                {
                    "status": "PASS",
                    "reasons": [],
                    "artifact_hash": "sha256:calibration",
                    "content_hash_present": True,
                    "market": "KRW-BTC",
                    "interval": "1m",
                    "expected_market": "KRW-BTC",
                    "expected_interval": "1m",
                    "expected_fill_reference_policy": "next_candle_open",
                    "artifact_fill_reference_policy": "next_candle_open",
                    "sample_count": 30,
                    "min_sample_count": 30,
                    "quality_gate_status": "PASS",
                }
            ],
        },
    }
    payload.update(overrides)
    return payload


def test_research_only_warn_calibration_is_not_production_required() -> None:
    result = validate_production_calibration_policy(
        _candidate(
            deployment_tier="research_only",
            execution_calibration_required=False,
            execution_calibration_strictness="warn",
            execution_calibration_gate={"status": "MISSING", "reasons": ["execution_calibration_missing"]},
        )
    )

    assert result.status == "NOT_REQUIRED"
    assert result.required is False


def test_production_policy_fails_closed_on_missing_hash_quality_or_samples() -> None:
    gate = {
        "status": "FAIL",
        "reasons": ["execution_calibration_quality_gate_not_passed"],
        "scenario_gates": [
            {
                "status": "FAIL",
                "reasons": ["execution_calibration_quality_gate_not_passed"],
                "content_hash_present": False,
                "market": "KRW-BTC",
                "interval": "1m",
                "expected_market": "KRW-BTC",
                "expected_interval": "1m",
                "sample_count": 10,
                "min_sample_count": 30,
                "quality_gate_status": "FAIL",
            }
        ],
    }

    result = validate_production_calibration_policy(_candidate(execution_calibration_gate=gate))

    assert result.status == "FAIL"
    assert "production_execution_calibration_hash_missing" in result.reasons
    assert "execution_calibration_quality_gate_not_passed" in result.reasons
    assert "execution_calibration_sample_count_below_required" in result.reasons


def test_production_policy_rejects_multiple_calibration_hashes() -> None:
    gate = _candidate()["execution_calibration_gate"]
    gate = dict(gate)
    gate["artifact_hashes"] = ["sha256:a", "sha256:b"]
    gate["scenario_gates"] = [
        {**gate["scenario_gates"][0], "artifact_hash": "sha256:a"},
        {**gate["scenario_gates"][0], "artifact_hash": "sha256:b"},
    ]

    result = validate_production_calibration_policy(_candidate(execution_calibration_gate=gate))

    assert result.status == "FAIL"
    assert result.artifact_hash is None
    assert "production_execution_calibration_hash_inconsistent" in result.reasons


def test_production_example_manifest_is_explicitly_calibrated() -> None:
    manifest = load_manifest(Path("examples/research/sma_filter_manifest.production.example.json"))

    assert manifest.deployment_tier == "paper_candidate"
    assert manifest.execution_model.source == "execution_model"
    assert manifest.execution_model.calibration_required is True
    assert manifest.execution_model.calibration_strictness == "fail"
    assert manifest.execution_timing.min_execution_reality_level_for_promotion == "latency_adjusted_top_of_book"
    assert manifest.dataset.top_of_book is not None
    assert manifest.dataset.top_of_book.required is True
    canonical = manifest.canonical_payload()
    assert canonical["deployment_tier"] == "paper_candidate"
    assert canonical["execution_model"]["calibration_required"] is True
