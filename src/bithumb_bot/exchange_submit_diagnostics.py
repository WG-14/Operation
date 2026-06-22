from __future__ import annotations

from typing import Any, Mapping, Sequence


SUBMIT_NOT_REACHED = "submit_not_reached"
EXCHANGE_REJECTED = "exchange_rejected"
SUBMITTED_NO_FILL = "submitted_no_fill"
SUBMITTED_FILLED = "submitted_filled"


def classify_exchange_submit_reachability(
    *,
    local_order: Mapping[str, object] | None,
    order_events: Sequence[Mapping[str, object]] = (),
    broker_recent_orders: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    if not local_order:
        return {
            "reason_code": SUBMIT_NOT_REACHED,
            "exchange_submit_reached": False,
            "matched_by": "none",
        }
    client_order_id = str(local_order.get("client_order_id") or "").strip()
    exchange_order_id = str(local_order.get("exchange_order_id") or "").strip()
    submit_started = any(
        str(event.get("event_type") or "").strip() in {"submit_started", "submit_attempt_recorded"}
        for event in order_events
    )
    remote = _match_remote(
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        broker_recent_orders=broker_recent_orders,
    )
    if remote is None and not exchange_order_id and not submit_started:
        return {
            "reason_code": SUBMIT_NOT_REACHED,
            "exchange_submit_reached": False,
            "client_order_id": client_order_id,
            "matched_by": "none",
        }
    if remote is None:
        status = str(local_order.get("status") or "").strip().upper()
        if status in {"REJECTED", "FAILED"}:
            return {
                "reason_code": EXCHANGE_REJECTED,
                "exchange_submit_reached": True,
                "client_order_id": client_order_id,
                "exchange_order_id": exchange_order_id or None,
                "matched_by": "local_order_status",
            }
        return {
            "reason_code": SUBMITTED_NO_FILL,
            "exchange_submit_reached": True,
            "client_order_id": client_order_id,
            "exchange_order_id": exchange_order_id or None,
            "matched_by": "local_submit_event",
        }
    remote_status = str(remote.get("status") or "").strip().upper()
    filled_qty = _float(remote.get("qty_filled", remote.get("filled_qty", 0.0)))
    rejected = remote_status in {"REJECTED", "FAILED", "CANCELED", "CANCELLED"} and filled_qty <= 0.0
    return {
        "reason_code": EXCHANGE_REJECTED if rejected else SUBMITTED_FILLED if filled_qty > 0.0 else SUBMITTED_NO_FILL,
        "exchange_submit_reached": True,
        "client_order_id": client_order_id,
        "exchange_order_id": str(remote.get("exchange_order_id") or exchange_order_id or "") or None,
        "broker_status": remote_status or None,
        "broker_filled_qty": filled_qty,
        "matched_by": "broker_recent_orders",
    }


def _match_remote(
    *,
    client_order_id: str,
    exchange_order_id: str,
    broker_recent_orders: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    for order in broker_recent_orders:
        remote_client = str(order.get("client_order_id") or "").strip()
        remote_exchange = str(order.get("exchange_order_id") or "").strip()
        if client_order_id and remote_client == client_order_id:
            return order
        if exchange_order_id and remote_exchange == exchange_order_id:
            return order
    return None


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "EXCHANGE_REJECTED",
    "SUBMITTED_NO_FILL",
    "SUBMIT_NOT_REACHED",
    "classify_exchange_submit_reachability",
]
