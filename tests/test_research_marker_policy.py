from __future__ import annotations

import pytest

from tests.factories.research_reports import assert_fast_research_workload, minimal_research_report
from tests.policy.research_runner_policy import discover_policy_violations


def test_direct_production_research_entrypoints_have_expensive_markers() -> None:
    assert discover_policy_violations() == []


def test_fast_research_workload_budget_rejects_large_strategy_run_count() -> None:
    report = minimal_research_report()
    report["workload_estimate"]["estimated_strategy_runs"] = 4

    with pytest.raises(AssertionError):
        assert_fast_research_workload(report)


def test_fast_research_workload_budget_rejects_tick_and_matrix_growth() -> None:
    report = minimal_research_report()
    report["workload_estimate"].update(
        {
            "candidate_count": 2,
            "scenario_count": 2,
            "split_count": 2,
            "estimated_strategy_runs": 2,
            "estimated_tick_events": 10_001,
        }
    )

    with pytest.raises(AssertionError):
        assert_fast_research_workload(report)


def test_fast_research_workload_budget_rejects_walk_forward_and_complete_external_audit() -> None:
    walk_forward_report = minimal_research_report()
    walk_forward_report["workload_estimate"]["walk_forward_window_count"] = 1
    with pytest.raises(AssertionError):
        assert_fast_research_workload(walk_forward_report)

    audit_report = minimal_research_report()
    audit_report["workload_estimate"]["audit_mode"] = "complete_external"
    with pytest.raises(AssertionError):
        assert_fast_research_workload(audit_report)


def test_fast_research_workload_budget_rejects_full_report_detail_and_full_decision_jsonl() -> None:
    detail_report = minimal_research_report()
    detail_report["workload_estimate"]["report_detail"] = "full"
    with pytest.raises(AssertionError):
        assert_fast_research_workload(detail_report)

    jsonl_report = minimal_research_report()
    jsonl_report["workload_estimate"]["full_decisions_external_jsonl"] = True
    with pytest.raises(AssertionError):
        assert_fast_research_workload(jsonl_report)
