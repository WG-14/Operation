from __future__ import annotations

from typing import Any

from bithumb_bot.config import settings
from bithumb_bot.execution_service import build_execution_decision_summary


def plan_legacy_context(
    planner: Any,
    conn: Any,
    *,
    decision_context: dict[str, object],
    signal: str,
    reason: str,
    updated_ts: int,
) -> Any:
    """Run legacy context planning as non-promotable diagnostic compatibility output."""
    context = dict(decision_context)
    try:
        readiness_payload = planner.readiness_snapshot_builder(conn).as_dict()
        strategy_performance_gate = None
        if planner.live_real_target_delta_performance_gate_applies():
            strategy_performance_gate = planner.performance_gate_evaluator(
                conn,
                strategy_name=str(settings.STRATEGY_NAME),
                pair=str(settings.PAIR),
            )
        raw_signal_for_target = str(
            context.get("raw_signal") or context.get("base_signal") or signal
        )
        reference_price = context.get("market_price", context.get("last_close", context.get("close")))
        target_resolution = planner.target_state_resolver(
            conn,
            readiness_payload=readiness_payload,
            reference_price=reference_price,
            raw_signal=raw_signal_for_target,
            updated_ts=int(updated_ts),
        )
        previous_target_exposure_krw = target_resolution.get("previous_target_exposure_krw")
        target_policy_metadata = dict(target_resolution.get("target_policy_metadata", {}))
        readiness_payload = {**readiness_payload, **target_policy_metadata}
        summary_context = dict(context)
        legacy_builder = (
            build_execution_decision_summary
            if planner.summary_builder is planner.typed_summary_builder
            else planner.summary_builder
        )
        execution_decision_summary = legacy_builder(
            decision_context=summary_context,
            readiness_payload=readiness_payload,
            raw_signal=raw_signal_for_target,
            final_signal=signal,
            final_reason=reason,
            previous_target_exposure_krw=(
                None if previous_target_exposure_krw is None else float(previous_target_exposure_krw)
            ),
            strategy_performance_gate=strategy_performance_gate,
        )
        summary_context["legacy_context_planning_used"] = True
        summary_context["compatibility_fallback"] = True
        summary_context["promotion_grade"] = False
        summary_context["artifact_grade"] = "diagnostic_only"
        summary_context["authority_plane"] = "compatibility_context"
        summary_context["execution_evidence_source"] = "diagnostic_context_fallback"
        summary_context["promotion_rejection_reason"] = "legacy_context_planning_diagnostic_only"
        summary_context["recommended_next_action"] = "regenerate_decision_with_typed_execution_authority"
        context = planner.persistence_context_builder(
            decision_context=summary_context,
            execution_decision_summary=execution_decision_summary,
            readiness_payload=readiness_payload,
            target_policy_metadata=target_policy_metadata,
        )
        execution_decision = dict(context["execution_decision"])
        return planner.result_cls(
            context=context,
            execution_decision=execution_decision,
            execution_decision_summary=execution_decision_summary,
            readiness_payload=readiness_payload,
            target_policy_metadata=target_policy_metadata,
        )
    except Exception as exc:
        return planner.fail_closed_context(
            decision_context=context,
            reason_code="execution_decision_unavailable",
            exc=exc,
        )
