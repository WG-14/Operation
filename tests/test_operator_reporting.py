from __future__ import annotations

from bithumb_bot.runtime.operator_event_composer import RuntimeOperatorEventComposer


def test_daily_participation_event_does_not_claim_fill_guarantee() -> None:
    event = RuntimeOperatorEventComposer("KRW-BTC").daily_participation_status_event(
        count_basis="filled",
        days_with_intent=3,
        days_with_filled_execution=1,
        zero_filled_days=2,
        max_consecutive_zero_filled_days=2,
        target_status="FAIL",
    )

    assert event["not_a_fill_guarantee"] is True
    assert "guarantee" in event["operator_compact_summary"]
    assert "fill guarantee" not in event["operator_compact_summary"]


def test_daily_participation_status_event_is_emitted_from_runtime_summary() -> None:
    from bithumb_bot.research.report_writer import summarize_report_candidate

    summary = summarize_report_candidate(
        {
            "market": "KRW-BTC",
            "validation_metrics_v2": {
                "participation": {
                    "count_basis": "filled",
                    "days_with_intent": 3,
                    "days_with_filled_execution": 1,
                    "zero_filled_days": 2,
                    "max_consecutive_zero_filled_days": 2,
                    "not_a_fill_guarantee": True,
                }
            },
        }
    )

    assert summary["operator_events"][0]["event_type"] == "daily_participation_status"


def test_daily_participation_event_keeps_zero_filled_days_visible() -> None:
    event = RuntimeOperatorEventComposer("KRW-BTC").daily_participation_status_event(
        count_basis="intent",
        days_with_intent=3,
        days_with_filled_execution=1,
        zero_filled_days=2,
        max_consecutive_zero_filled_days=2,
        target_status="FAIL",
    )

    assert event["zero_filled_days"] == 2
    assert "zero_filled_days=2" in event["operator_compact_summary"]
    assert "fill guarantee" not in event["operator_compact_summary"]
