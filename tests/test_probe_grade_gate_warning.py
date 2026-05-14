from __future__ import annotations

from bithumb_bot.research.cli import _print_report_summary
from bithumb_bot.research.promotion_gate import evaluate_candidate_for_promotion
from bithumb_bot.research.validation_protocol import _probe_grade_gate_warnings

from bithumb_bot.research.experiment_manifest import parse_manifest


def _manifest(*, deployment_tier: str = "research_only") -> dict[str, object]:
    return {
        "experiment_id": "probe_warning_test",
        "hypothesis": "Probe gates are visible.",
        "strategy_name": "sma_with_filter",
        "deployment_tier": deployment_tier,
        "market": "KRW-BTC",
        "interval": "1m",
        "dataset": {
            "source": "sqlite_candles",
            "snapshot_id": "candles_v1",
            "train": {"start": "2023-01-01", "end": "2023-01-01"},
            "validation": {"start": "2023-01-02", "end": "2023-01-02"},
        },
        "parameter_space": {
            "SMA_SHORT": [7],
            "SMA_LONG": [30],
            "SMA_FILTER_GAP_MIN_RATIO": [0.0012],
        },
        "cost_model": {"fee_rate": 0.0004, "slippage_bps": [10]},
        "acceptance_gate": {
            "min_trade_count": 1,
            "max_mdd_pct": 15,
            "min_profit_factor": 1.0,
            "oos_return_must_be_positive": True,
            "parameter_stability_required": False,
        },
    }


def test_research_only_min_trade_count_one_emits_probe_grade_warning() -> None:
    payload = _manifest(deployment_tier="research_only")
    payload["acceptance_gate"]["min_trade_count"] = 1
    payload["acceptance_gate"]["min_profit_factor"] = 1.0
    payload["acceptance_gate"]["metrics_contract_required"] = False
    payload["acceptance_gate"]["walk_forward_required"] = False
    payload.pop("walk_forward", None)
    manifest = parse_manifest(payload)

    assert _probe_grade_gate_warnings(manifest) == [
        "probe_grade_gate_detected",
        "probe_grade_pass_not_promotable",
    ]


def test_cli_report_summary_exposes_probe_warning(capsys) -> None:
    _print_report_summary(
        "RESEARCH-BACKTEST",
        {
            "experiment_id": "exp",
            "manifest_hash": "sha256:manifest",
            "dataset_snapshot_id": "snap",
            "dataset_content_hash": "sha256:dataset",
            "candidate_count": 0,
            "gate_result": "FAIL",
            "warnings": ["probe_grade_gate_detected", "probe_grade_pass_not_promotable"],
            "candidates": [],
            "artifact_paths": {},
        },
    )

    output = capsys.readouterr().out
    assert "warnings=probe_grade_gate_detected,probe_grade_pass_not_promotable" in output


def test_promotion_summary_does_not_present_probe_grade_pass_as_promotable() -> None:
    allowed, reasons = evaluate_candidate_for_promotion(
        {
            "acceptance_gate_result": "PASS",
            "validation_metrics": {"trade_count": 1},
            "warnings": ["probe_grade_gate_detected", "probe_grade_pass_not_promotable"],
        }
    )

    assert allowed is False
    assert "probe_grade_pass_not_promotable" in reasons
