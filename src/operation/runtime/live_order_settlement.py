from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..broker.base import BrokerFill, BrokerOrder
from ..db_core import (
    FILL_FEE_ACCOUNTING_STATUS_BLOCKED,
    FILL_FEE_ACCOUNTING_STATUS_FINALIZED,
    FILL_FEE_ACCOUNTING_STATUS_PENDING,
    compute_accounting_replay,
)
from ..fee_observation import fee_accounting_status
from ..oms import TERMINAL_ORDER_STATUSES
from ..order_settlement import OrderSettlementCoordinator, OrderSettlementResult, SettlementBarrierConfig
from ..runtime_readiness import compute_runtime_readiness_snapshot


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fill_fee_status(fill: BrokerFill) -> str:
    return fee_accounting_status(
        fee=getattr(fill, "fee", None),
        fee_status=getattr(fill, "fee_status", "unknown"),
        price=getattr(fill, "price", None),
        qty=getattr(fill, "qty", None),
        fee_source=getattr(fill, "fee_source", None),
        fee_confidence=getattr(fill, "fee_confidence", None),
        provenance=getattr(fill, "fee_provenance", None),
        reason=getattr(fill, "fee_validation_reason", None),
        checks=getattr(fill, "fee_validation_checks", None),
    )


def _order_fill_evidence(*, order: BrokerOrder | None, fills: list[BrokerFill]) -> dict[str, Any]:
    status = str(getattr(order, "status", "") or "unknown").upper()
    order_terminal = status in TERMINAL_ORDER_STATUSES or status in {
        "FILLED",
        "CANCELED",
        "CANCELLED",
        "REJECTED",
        "DONE",
    }
    order_filled_qty = _float_or_none(getattr(order, "qty_filled", None))
    fill_qty = sum(float(getattr(fill, "qty", 0.0) or 0.0) for fill in fills)
    fill_count = len(fills)
    complete_fill_set_available = bool(
        fill_count > 0
        and (order_filled_qty is None or abs(float(order_filled_qty) - float(fill_qty)) <= 1e-12)
    )
    accounting_statuses = [_fill_fee_status(fill) for fill in fills]
    fee_pending = any(status == "fee_pending" for status in accounting_statuses)
    fee_blocked = any(status == "fee_validation_blocked" for status in accounting_statuses)
    fee_finalized = bool(fill_count > 0 and not fee_pending and not fee_blocked)
    order_level_paid_fee_present = any(
        str(getattr(fill, "fee_source", "") or "") == "order_level_paid_fee"
        or "order_level_paid_fee" in str(getattr(fill, "fee_provenance", "") or "")
        for fill in fills
    )
    trade_level_fee_present = any(
        getattr(fill, "fee", None) is not None
        and str(getattr(fill, "fee_source", "") or "") == "trade_level_fee"
        for fill in fills
    )
    return {
        "order_state": status,
        "order_terminal": order_terminal,
        "fill_count": fill_count,
        "fill_set_complete": bool(order_terminal and complete_fill_set_available),
        "trade_level_fee_present": trade_level_fee_present,
        "paid_fee_present": bool(fee_finalized),
        "order_level_paid_fee_present": order_level_paid_fee_present,
        "complete_fill_set_available": complete_fill_set_available,
        "single_fill_deterministic": fill_count == 1 and fee_finalized,
        "multi_fill_deterministic_allocation_available": fill_count > 1 and fee_finalized,
        "fee_finalized": fee_finalized,
        "fee_pending_retryable": bool(fee_pending and not fee_blocked),
        "fee_pending_hard_blocked": bool(fee_blocked),
        "fee_state": "blocked" if fee_blocked else ("pending" if fee_pending or fill_count <= 0 else "finalized"),
        "hard_blocked": bool(fee_blocked),
    }


def _db_order_evidence(conn: Any, *, client_order_id: str) -> dict[str, Any]:
    fill_summary = conn.execute(
        """
        SELECT
            COUNT(*) AS fill_count,
            COALESCE(SUM(CASE WHEN fee_accounting_status=? THEN 1 ELSE 0 END), 0)
                AS finalized_count,
            COALESCE(SUM(CASE WHEN fee_accounting_status=? THEN 1 ELSE 0 END), 0)
                AS pending_count,
            COALESCE(SUM(CASE WHEN fee_accounting_status=? THEN 1 ELSE 0 END), 0)
                AS blocked_count
        FROM fills
        WHERE client_order_id=?
        """,
        (
            FILL_FEE_ACCOUNTING_STATUS_FINALIZED,
            FILL_FEE_ACCOUNTING_STATUS_PENDING,
            FILL_FEE_ACCOUNTING_STATUS_BLOCKED,
            client_order_id,
        ),
    ).fetchone()
    db_fill_count = int(fill_summary["fill_count"] or 0) if fill_summary is not None else 0
    finalized_count = int(fill_summary["finalized_count"] or 0) if fill_summary is not None else 0
    pending_count = int(fill_summary["pending_count"] or 0) if fill_summary is not None else 0
    blocked_count = int(fill_summary["blocked_count"] or 0) if fill_summary is not None else 0
    return {
        "principal_applied": db_fill_count > 0,
        "accounting_finalized": db_fill_count > 0 and finalized_count == db_fill_count,
        "db_fill_count": db_fill_count,
        "db_fee_finalized_count": finalized_count,
        "db_fee_pending_count": pending_count,
        "db_fee_blocked_count": blocked_count,
        "db_fee_state": "blocked" if blocked_count else ("pending" if pending_count or db_fill_count <= 0 else "finalized"),
    }


def _readiness_evidence(conn: Any) -> dict[str, Any]:
    readiness = compute_runtime_readiness_snapshot(conn)
    projection = dict(readiness.projection_convergence or {})
    broker_position = dict(readiness.broker_position_evidence or {})
    replay = compute_accounting_replay(conn)
    projected_total_qty = _float_or_none(projection.get("projected_total_qty"))
    portfolio_qty = _float_or_none(projection.get("portfolio_qty"))
    broker_qty = _float_or_none(broker_position.get("broker_qty"))
    projection_applied = bool(projection.get("converged"))
    broker_known = bool(broker_position.get("broker_qty_known") or "broker_qty" in broker_position)
    broker_local_converged = bool(
        projection_applied
        and broker_known
        and broker_qty is not None
        and portfolio_qty is not None
        and abs(float(broker_qty) - float(portfolio_qty)) <= 1e-12
    )
    return {
        "projection_applied": projection_applied,
        "projected_total_qty": projected_total_qty,
        "portfolio_qty": portfolio_qty,
        "broker_qty": broker_qty,
        "broker_local_converged": broker_local_converged,
        "accounting_projection_unresolved_fee_state": bool(replay.get("unresolved_fee_state")),
        "readiness_recovery_stage": readiness.recovery_stage,
        "readiness_projection_reason": str(projection.get("reason") or "unknown"),
    }


@dataclass(frozen=True)
class LiveOrderSettlementWrapper:
    broker: Any
    db_factory: Callable[[], Any]
    reconcile_with_broker: Callable[[Any], Any] | None = None
    coordinator: OrderSettlementCoordinator | None = None

    def __call__(self, trade: Mapping[str, Any]) -> OrderSettlementResult:
        client_order_id = str(trade.get("client_order_id") or "").strip()
        exchange_order_id = str(trade.get("exchange_order_id") or "").strip() or None
        coordinator = self.coordinator or OrderSettlementCoordinator(
            SettlementBarrierConfig(max_attempts=5, poll_intervals_ms=(100, 250, 500, 1000, 2000), deadline_ms=5000)
        )

        def _reconcile() -> Any:
            if self.reconcile_with_broker is not None:
                return self.reconcile_with_broker(self.broker)
            return None

        def _observe(_attempt_index: int) -> Mapping[str, Any]:
            order = self.broker.get_order(
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
            )
            fills = self.broker.get_fills(
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
                parse_mode="salvage",
            )
            conn = self.db_factory()
            try:
                evidence = {
                    **_order_fill_evidence(order=order, fills=list(fills)),
                    **_db_order_evidence(conn, client_order_id=client_order_id),
                    **_readiness_evidence(conn),
                    "reason_code": "settlement_evidence_pending",
                }
                if evidence["fee_state"] == "blocked":
                    evidence["reason_code"] = "fee_evidence_hard_blocked"
                elif (
                    evidence["fee_state"] == "finalized"
                    and evidence["principal_applied"]
                    and evidence["accounting_finalized"]
                    and evidence["projection_applied"]
                    and evidence["broker_local_converged"]
                ):
                    evidence["reason_code"] = "settlement_evidence_complete"
                return evidence
            finally:
                close = getattr(conn, "close", None)
                if callable(close):
                    close()

        return coordinator.settle(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            observe=_observe,
            reconcile=_reconcile,
        )


__all__ = ["LiveOrderSettlementWrapper"]
