from __future__ import annotations

from bithumb_bot.exchange_submit_diagnostics import (
    EXCHANGE_REJECTED,
    SUBMITTED_NO_FILL,
    SUBMIT_NOT_REACHED,
    classify_exchange_submit_reachability,
)


def test_submit_not_reached_when_local_submit_is_absent() -> None:
    result = classify_exchange_submit_reachability(
        local_order={
            "client_order_id": "cid-1",
            "status": "INTENT_CREATED",
            "exchange_order_id": "",
        },
        order_events=[{"event_type": "intent_created", "client_order_id": "cid-1"}],
        broker_recent_orders=[],
    )

    assert result["reason_code"] == SUBMIT_NOT_REACHED
    assert result["exchange_submit_reached"] is False


def test_exchange_rejected_classified_from_broker_recent_order() -> None:
    result = classify_exchange_submit_reachability(
        local_order={
            "client_order_id": "cid-2",
            "status": "SUBMITTED",
            "exchange_order_id": "ex-2",
        },
        order_events=[{"event_type": "submit_started", "client_order_id": "cid-2"}],
        broker_recent_orders=[
            {
                "client_order_id": "cid-2",
                "exchange_order_id": "ex-2",
                "status": "REJECTED",
                "qty_filled": 0.0,
            }
        ],
    )

    assert result["reason_code"] == EXCHANGE_REJECTED
    assert result["exchange_submit_reached"] is True
    assert result["matched_by"] == "broker_recent_orders"


def test_submitted_no_fill_classified_from_recent_order() -> None:
    result = classify_exchange_submit_reachability(
        local_order={
            "client_order_id": "cid-3",
            "status": "SUBMITTED",
            "exchange_order_id": "ex-3",
        },
        order_events=[{"event_type": "submit_started", "client_order_id": "cid-3"}],
        broker_recent_orders=[
            {
                "client_order_id": "cid-3",
                "exchange_order_id": "ex-3",
                "status": "NEW",
                "qty_filled": 0.0,
            }
        ],
    )

    assert result["reason_code"] == SUBMITTED_NO_FILL
    assert result["exchange_submit_reached"] is True
