from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from operation import runtime_state
from operation.runtime.cycle_artifact_assembler import RuntimeCycleArtifactAssembler
from operation.runtime.cycle_pipeline import RuntimeCyclePipeline
from operation.runtime.no_submit_diagnostic import diagnostic_for_stage
from tests.test_runtime_cycle_artifact_assembler import _decision_result
from operation.runtime.execution_coordinator import ExecutionCycleResult


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


def test_sync_failed_records_diagnostic_stage() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="skip:sync_failed",
        candle_ts=None,
        stage="market",
        reason_code="sync_failed",
    ).as_dict()

    assert diagnostic["stage"] == "market"
    assert diagnostic["reason_code"] == "sync_failed"
    assert "runtime_data_availability_report_hash" in diagnostic["missing_because"]


def test_stale_candle_skip_records_market_stage() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="skip:stale_candle",
        candle_ts=123,
        stage="market",
        reason_code="stale_candle_detected",
        runtime_data_availability_report_hash="sha256:runtime-data",
    ).as_dict()

    assert diagnostic["stage"] == "market"
    assert diagnostic["upstream_hashes"]["runtime_data_availability_report_hash"] == "sha256:runtime-data"


def test_market_safety_block_records_risk_stage() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="halt:market_runtime",
        candle_ts=123,
        stage="risk",
        reason_code="market_runtime_safety_block",
        strategy_decision_hash="sha256:safety",
    ).as_dict()

    assert diagnostic["stage"] == "risk"
    assert diagnostic["upstream_hashes"]["strategy_decision_hash"] == "sha256:safety"


def test_insufficient_signal_history_records_strategy_stage() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="skip:insufficient_signal_history",
        candle_ts=123,
        stage="strategy",
        reason_code="insufficient_signal_history",
        runtime_data_availability_report_hash="sha256:runtime-data",
    ).as_dict()

    assert diagnostic["stage"] == "strategy"
    assert diagnostic["reason_code"] == "insufficient_signal_history"


def test_halt_execution_path_keeps_diagnostic() -> None:
    diagnostic = diagnostic_for_stage(
        cycle_id="halt:submit_authority_blocked",
        candle_ts=123,
        stage="submit",
        reason_code="submit_authority_blocked",
        strategy_decision_hash="sha256:strategy",
        execution_plan_bundle_hash="sha256:bundle",
        submit_authority_reason="submit_authority_blocked",
    ).as_dict()

    assert diagnostic["stage"] == "submit"
    assert diagnostic["submit_authority_reason"] == "submit_authority_blocked"


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


def _pipeline_runner(*, now: float = 100.0):
    class _Runner:
        runtime_checkpoint = object()
        runtime_events = object()

        def __init__(self) -> None:
            self.container = SimpleNamespace(
                settings_obj=SimpleNamespace(PAIR="KRW-BTC", INTERVAL="1m"),
                interval_parser=lambda _interval: 60,
                clock=lambda: now,
                runtime_dependency_manifest_hash="sha256:deps",
            )
            self.artifacts = []

        def _record_artifact(self, cycle_id: str, **kwargs: object):
            from operation.runtime.lifecycle_artifacts import RuntimeCycleArtifact

            artifact = RuntimeCycleArtifact(
                cycle_id=cycle_id,
                runtime_dependency_manifest_hash=self.container.runtime_dependency_manifest_hash,
                **kwargs,
            )
            self.artifacts.append(artifact)
            return artifact

    return _Runner()


def test_trading_halted_exit_records_diagnostic_artifact(monkeypatch) -> None:
    runner = _pipeline_runner()
    monkeypatch.setattr(
        runtime_state,
        "snapshot",
        lambda: SimpleNamespace(trading_enabled=False, retry_at_epoch_sec=float("inf")),
    )

    artifact = RuntimeCyclePipeline(runner).run_once()

    assert artifact is not None
    diagnostic = artifact.as_dict()["runtime_cycle_diagnostic"]
    assert diagnostic["stage"] == "risk"
    assert diagnostic["reason_code"] == "trading_halted_indefinitely"
    assert "upstream_hashes" in diagnostic
    assert diagnostic["missing_because"]["strategy_decision_hash"] == "risk"


def test_failsafe_pause_records_diagnostic_artifact(monkeypatch) -> None:
    runner = _pipeline_runner(now=100.0)
    monkeypatch.setattr(
        runtime_state,
        "snapshot",
        lambda: SimpleNamespace(trading_enabled=False, retry_at_epoch_sec=130.0),
    )

    artifact = RuntimeCyclePipeline(runner).run_once()

    assert artifact is not None
    diagnostic = artifact.as_dict()["runtime_cycle_diagnostic"]
    assert diagnostic["stage"] == "risk"
    assert diagnostic["reason_code"] == "failsafe_pause_retry_window_not_reached"
    assert "upstream_hashes" in diagnostic
    assert diagnostic["missing_because"]["runtime_data_availability_report_hash"] == "risk"


def test_cycle_pipeline_record_artifact_calls_attach_diagnostic() -> None:
    path = Path("src/operation/runtime/cycle_pipeline.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "_record_artifact"
        ):
            continue
        if not any(keyword.arg == "runtime_cycle_diagnostic" for keyword in node.keywords):
            missing.append(node.lineno)

    assert missing == []


def test_cycle_pipeline_has_no_bare_return_none_early_exit() -> None:
    path = Path("src/operation/runtime/cycle_pipeline.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "run_once":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant) and child.value.value is None:
                missing.append(child.lineno)

    assert missing == []
