from __future__ import annotations

from bithumb_bot.runtime.cycle_artifact_assembler import RuntimeCycleArtifactAssembler
from bithumb_bot.runtime.no_submit_diagnostic import diagnostic_for_stage
from tests.test_runtime_cycle_artifact_assembler import _decision_result
from bithumb_bot.runtime.execution_coordinator import ExecutionCycleResult


def test_runtime_data_preflight_failure_records_diagnostic_stage() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="skip:runtime_data_preflight_failed",
        candle_ts=123,
        stage="market",
        reason_code="runtime_data_preflight_failed",
        runtime_data_availability_report_hash="sha256:runtime-data",
    ).as_dict()

    assert diagnostic["stage"] == "market"
    assert diagnostic["reason_code"] == "runtime_data_preflight_failed"
    assert diagnostic["upstream_hashes"]["runtime_data_availability_report_hash"] == "sha256:runtime-data"


def test_safety_block_records_no_submit_diagnostic() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="safety:block",
        candle_ts=123,
        stage="risk",
        reason_code="daily_loss_limit",
    ).as_dict()

    assert diagnostic["stage"] == "risk"
    assert diagnostic["missing_because"]["strategy_decision_hash"] == "risk"


def test_strategy_hold_records_daily_count_and_policy_hashes() -> None:
    artifact = RuntimeCycleArtifactAssembler().from_cycle_results(
        cycle_id="checkpoint:processed",
        startup_state="READY",
        decision_result=_decision_result(
            signal="HOLD",
            reason="strategy_hold",
            decision_context={
                "runtime_data_availability_report_hash": "sha256:runtime-data",
                "daily_count_snapshot_hash": "sha256:daily-count",
            },
        ),
    ).as_dict()

    diagnostic = artifact["runtime_cycle_diagnostic"]
    assert diagnostic["stage"] == "strategy"
    assert diagnostic["upstream_hashes"]["daily_count_snapshot_hash"] == "sha256:daily-count"
    assert diagnostic["strategy_decision_hash"] == "sha256:strategy"


def test_submit_authority_block_records_submit_reason() -> None:
    artifact = RuntimeCycleArtifactAssembler().from_cycle_results(
        cycle_id="checkpoint:processed",
        startup_state="READY",
        decision_result=_decision_result(),
        execution_result=ExecutionCycleResult(
            candle_ts=123,
            decision_id=7,
            planning_status="submit_authority_blocked",
            submit_expected=False,
            submitted=False,
            post_trade_reconciled=False,
            mark_processed_allowed=True,
        ),
    ).as_dict()

    diagnostic = artifact["runtime_cycle_diagnostic"]
    assert diagnostic["stage"] == "submit"
    assert diagnostic["submit_authority_reason"] == "submit_authority_blocked"
    assert diagnostic["upstream_hashes"]["execution_plan_bundle_hash"] == "sha256:bundle"
