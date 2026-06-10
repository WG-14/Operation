from __future__ import annotations

import json
from pathlib import Path

from bithumb_bot.research.report_writer import (
    ResearchReportPaths,
    persist_final_research_report_observability,
    summarize_candidate_result,
    summarize_derived_candidate,
    summarize_report_candidate,
)


def _paths(tmp_path: Path) -> ResearchReportPaths:
    return ResearchReportPaths(
        derived_path=tmp_path / "derived_candidates.json",
        report_path=tmp_path / "backtest_report.json",
        candidate_events_path=tmp_path / "candidate_events.jsonl",
        candidate_results_dir=tmp_path / "candidate_results",
        candidate_failures_dir=tmp_path / "candidate_failures",
        trace_manifest_path=tmp_path / "trace_manifest.json",
    )


def _artifact_summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "derived_candidates_path": "/tmp/derived_candidates.json",
        "derived_candidates_ref": "derived/research/test/derived_candidates.json",
        "derived_candidates_hash": "sha256:" + "0" * 64,
        "derived_candidates_bytes": 17,
        "report_path": "/tmp/backtest_report.json",
        "report_ref": "reports/research/test/backtest_report.json",
        "report_bytes": 0,
        "artifact_file_count": 2,
        "artifact_total_bytes": 17,
        "write_wall_seconds": 0.25,
    }


def test_report_write_stage_timing_payload_matches_artifact_summary(tmp_path: Path) -> None:
    payload = {
        "experiment_id": "contract",
        "candidates": [],
        "execution_observability": {
            "stage_timings": [
                {"stage": "load_split", "wall_seconds": 0.1},
                {"stage": "report_write", "wall_seconds": 0.2},
            ]
        },
    }

    _, summary = persist_final_research_report_observability(
        paths=_paths(tmp_path),
        report_payload=payload,
        artifact_write_summary=_artifact_summary(),
        artifact_total_bytes_base=17,
    )

    report_write = [
        item for item in payload["execution_observability"]["stage_timings"] if item["stage"] == "report_write"
    ][0]
    assert report_write["artifact_total_bytes"] == summary["artifact_total_bytes"]
    assert report_write["artifact_file_count"] == summary["artifact_file_count"]
    assert report_write["derived_candidates_bytes"] == summary["derived_candidates_bytes"]
    assert report_write["report_bytes"] == summary["report_bytes"]


def test_persist_final_research_report_observability_updates_persisted_payload(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    payload = {
        "experiment_id": "contract",
        "candidates": [],
        "execution_observability": {
            "stage_timings": [
                {"stage": "report_write", "wall_seconds": 0.2},
            ]
        },
    }

    content_hash, summary = persist_final_research_report_observability(
        paths=paths,
        report_payload=payload,
        artifact_write_summary=_artifact_summary(),
        artifact_total_bytes_base=17,
    )

    persisted = json.loads(paths.report_path.read_text(encoding="utf-8"))
    assert persisted["content_hash"] == content_hash
    assert persisted["artifact_write_summary"] == summary
    assert persisted["artifact_observability"]["report_write"] == summary


def test_summary_report_uses_candidate_summary() -> None:
    candidate = {
        "candidate_id": "candidate_001",
        "acceptance_gate_result": "PASS",
        "validation_metrics_v2": {"total_return_pct": 1.0},
        "decisions": [{"ts": 1}],
        "equity_curve": [{"ts": 1, "equity": 1.0}],
    }

    summary = summarize_report_candidate(candidate)

    assert summary["candidate_id"] == "candidate_001"
    assert summary["acceptance_gate_result"] == "PASS"
    assert summary["validation_metrics_v2"] == {"total_return_pct": 1.0}
    assert summary["candidate_payload_hash"].startswith("sha256:")
    assert "decisions" not in summary
    assert "equity_curve" not in summary


def test_summary_derived_candidates_are_bounded() -> None:
    candidate = {
        "parameter_candidate_id": "candidate_001",
        "candidate_profile_hash": "sha256:profile",
        "scenario_results": [
            {
                "scenario_id": "scenario_001",
                "validation_equity_curve": [{"ts": 1, "equity": 1.0}],
                "train_equity_curve": [{"ts": 1, "equity": 1.0}],
                "final_holdout_equity_curve": [{"ts": 1, "equity": 1.0}],
                "equity_curve_hash": "sha256:equity",
                "retained_detail_summary": {"retained_equity_point_count": 0},
            }
        ],
        "decisions": [{"ts": 1}],
    }

    summary = summarize_derived_candidate(candidate, "summary")

    assert summary["derived_detail_policy"] == "summary_bounded"
    assert summary["candidate_result_detail_policy"] == "summary_bounded"
    assert summary["candidate_profile_hash"] == "sha256:profile"
    assert "decisions" not in summary
    scenario = summary["scenario_results"][0]
    assert scenario["train_equity_curve"] == []
    assert scenario["validation_equity_curve"] == []
    assert scenario["final_holdout_equity_curve"] == []
    assert scenario["equity_curve_hash"] == "sha256:equity"
    assert scenario["retained_detail_summary"] == {"retained_equity_point_count": 0}


def test_candidate_result_summary_is_reference_first_bounded() -> None:
    candidate = {
        "parameter_candidate_id": "candidate_001",
        "candidate_profile_hash": "sha256:profile",
        "behavior_hash": "sha256:behavior",
        "scenario_results": [
            {
                "scenario_id": "scenario_001",
                "validation_equity_curve": [{"ts": 1, "equity": 1.0}],
                "validation_execution_metadata": [{"ts": 1, "fill": "large"}],
                "behavior_hash": "sha256:scenario-behavior",
                "equity_curve_hash": "sha256:equity",
                "retained_detail_summary": {"retained_equity_point_count": 0},
            }
        ],
    }

    summary = summarize_candidate_result(candidate, "summary")

    assert summary["candidate_result_detail_policy"] == "summary_bounded"
    assert summary["candidate_profile_hash"] == "sha256:profile"
    assert summary["behavior_hash"] == "sha256:behavior"
    scenario = summary["scenario_results"][0]
    assert scenario["validation_equity_curve"] == []
    assert "validation_execution_metadata" not in scenario
    assert scenario["behavior_hash"] == "sha256:scenario-behavior"
    assert scenario["equity_curve_hash"] == "sha256:equity"
