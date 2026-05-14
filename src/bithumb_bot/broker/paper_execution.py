from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..execution_reality_contract import build_execution_reality_contract
from ..research.execution_model import ExecutionRequest, StressExecutionModel


@dataclass(frozen=True)
class PaperExecutionRequest:
    signal_ts: int
    decision_ts: int
    side: str
    requested_qty: float
    reference_price: float
    fee_rate: float
    slippage_bps: float
    best_bid: float | None = None
    best_ask: float | None = None
    spread_bps: float | None = None
    quote_source: str | None = None
    quote_age_ms: int | None = None
    execution_reality_level: str = "paper_top_of_book"
    execution_reality_contract: dict[str, Any] | None = None
    base_seed: int | None = None
    seed_derivation_inputs: dict[str, Any] | None = None


@dataclass(frozen=True)
class PaperExecutionResult:
    fill_status: str
    requested_qty: float
    filled_qty: float
    remaining_qty: float
    avg_fill_price: float | None
    fee: float
    latency_ms: int
    model_name: str
    model_version: str
    model_params_hash: str
    base_seed: int | None
    derived_seed_hash: str | None
    evidence: dict[str, Any]


class ImmediateTopOfBookPaperAdapter:
    name = "immediate_top_of_book"
    version = "paper_immediate_v1"

    def execute(self, request: PaperExecutionRequest) -> PaperExecutionResult:
        requested_qty = max(0.0, float(request.requested_qty))
        avg_fill_price = float(request.reference_price)
        fee = requested_qty * avg_fill_price * max(0.0, float(request.fee_rate))
        evidence = _base_evidence(
            request=request,
            model_name=self.name,
            model_version=self.version,
            model_params_hash="",
            fill_status="filled",
            filled_qty=requested_qty,
            remaining_qty=0.0,
            latency_ms=0,
            derived_seed_hash=None,
        )
        return PaperExecutionResult(
            fill_status="filled",
            requested_qty=requested_qty,
            filled_qty=requested_qty,
            remaining_qty=0.0,
            avg_fill_price=avg_fill_price,
            fee=fee,
            latency_ms=0,
            model_name=self.name,
            model_version=self.version,
            model_params_hash="",
            base_seed=None,
            derived_seed_hash=None,
            evidence=evidence,
        )


class StressPaperExecutionAdapter:
    def __init__(
        self,
        *,
        fee_rate: float,
        slippage_bps: float,
        latency_ms: int,
        partial_fill_rate: float,
        partial_fill_fraction: float,
        order_failure_rate: float,
        seed: int | None,
    ) -> None:
        self._model = StressExecutionModel(
            fee_rate=float(fee_rate),
            slippage_bps=float(slippage_bps),
            latency_ms=int(latency_ms),
            partial_fill_rate=float(partial_fill_rate),
            partial_fill_fraction=float(partial_fill_fraction),
            order_failure_rate=float(order_failure_rate),
            seed=seed,
        )

    def execute(self, request: PaperExecutionRequest) -> PaperExecutionResult:
        model = StressExecutionModel(
            fee_rate=self._model.fee_rate,
            slippage_bps=self._model.slippage_bps,
            latency_ms=self._model.latency_ms,
            partial_fill_rate=self._model.partial_fill_rate,
            partial_fill_fraction=self._model.partial_fill_fraction,
            order_failure_rate=self._model.order_failure_rate,
            market_order_extra_cost_bps=self._model.market_order_extra_cost_bps,
            seed=self._model.seed,
            seed_derivation_inputs=request.seed_derivation_inputs,
        )
        simulated = model.simulate(
            ExecutionRequest(
                signal_ts=int(request.signal_ts),
                decision_ts=int(request.decision_ts),
                side=str(request.side).upper(),
                reference_price=float(request.reference_price),
                requested_qty=float(request.requested_qty),
                fee_rate=float(request.fee_rate),
                best_bid=request.best_bid,
                best_ask=request.best_ask,
                spread_bps=request.spread_bps,
                quote_source=request.quote_source,
                quote_age_ms=request.quote_age_ms,
                execution_reality_level=request.execution_reality_level,
                top_of_book_source=request.quote_source,
            )
        )
        requested_qty = max(0.0, float(request.requested_qty))
        fill_ratio = (
            0.0
            if simulated.fill_status == "failed"
            else min(1.0, max(0.0, float(simulated.filled_qty) / float(simulated.requested_qty)))
            if float(simulated.requested_qty) > 0.0
            else 0.0
        )
        filled_qty = requested_qty * fill_ratio
        remaining_qty = max(0.0, requested_qty - filled_qty)
        avg_fill_price = simulated.avg_fill_price if filled_qty > 0.0 else None
        fee = (
            filled_qty * float(avg_fill_price) * max(0.0, float(request.fee_rate))
            if avg_fill_price is not None
            else 0.0
        )
        evidence = _base_evidence(
            request=request,
            model_name=simulated.model_name,
            model_version=simulated.model_version,
            model_params_hash=simulated.model_params_hash,
            fill_status=simulated.fill_status,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            latency_ms=simulated.latency_ms,
            derived_seed_hash=simulated.derived_seed_hash,
        )
        evidence["seed_derivation_inputs"] = simulated.seed_derivation_inputs
        evidence["stress_model_requested_qty"] = simulated.requested_qty
        evidence["stress_model_filled_qty"] = simulated.filled_qty
        evidence["stress_model_remaining_qty"] = simulated.remaining_qty
        return PaperExecutionResult(
            fill_status=simulated.fill_status,
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            avg_fill_price=avg_fill_price,
            fee=fee,
            latency_ms=int(simulated.latency_ms),
            model_name=simulated.model_name,
            model_version=simulated.model_version,
            model_params_hash=simulated.model_params_hash,
            base_seed=simulated.base_seed,
            derived_seed_hash=simulated.derived_seed_hash,
            evidence=evidence,
        )


def _base_evidence(
    *,
    request: PaperExecutionRequest,
    model_name: str,
    model_version: str,
    model_params_hash: str,
    fill_status: str,
    filled_qty: float,
    remaining_qty: float,
    latency_ms: int,
    derived_seed_hash: str | None,
) -> dict[str, Any]:
    contract = request.execution_reality_contract or build_execution_reality_contract(
        fill_reference_policy="paper_top_of_book",
        missing_quote_policy="fail",
        quote_source=request.quote_source or "unknown",
        quote_age_limit_ms=request.quote_age_ms,
        top_of_book_required=True,
        top_of_book_is_full_depth=False,
        depth_required=False,
        trade_tick_required=False,
        queue_position_required=False,
        intra_candle_path_available=False,
        fee_source="paper_runtime_settings",
        slippage_source="paper_runtime_settings",
        calibration_required=False,
        execution_reality_level=request.execution_reality_level,
        latency_model={"type": model_name, "latency_ms": int(latency_ms)},
        partial_fill_model={"type": model_name, "fill_status": fill_status},
        order_failure_model={"type": model_name, "fill_status": fill_status},
        extra={
            "quote_evidence_available": request.best_bid is not None and request.best_ask is not None,
            "depth_available": False,
            "trade_ticks_available": False,
            "queue_position_available": False,
            "market_impact_model_available": False,
            "intra_candle_path_required": False,
        },
    )
    return {
        "execution_model_name": model_name,
        "execution_model_version": model_version,
        "execution_model_params_hash": model_params_hash,
        "base_seed": request.base_seed,
        "derived_seed_hash": derived_seed_hash,
        "fill_status": fill_status,
        "requested_qty": float(request.requested_qty),
        "filled_qty": float(filled_qty),
        "remaining_qty": float(remaining_qty),
        "latency_ms": int(latency_ms),
        "best_bid": request.best_bid,
        "best_ask": request.best_ask,
        "spread_bps": request.spread_bps,
        "quote_source": request.quote_source or "unknown",
        "quote_age_ms": request.quote_age_ms,
        "execution_reality_level": request.execution_reality_level,
        "execution_reality_contract": contract,
        "execution_contract_hash": contract["execution_contract_hash"],
    }
