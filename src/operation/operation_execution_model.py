"""Runtime-owned deterministic stress execution model used by paper execution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .artifact_hashing import sha256_hex, sha256_prefixed

@dataclass(frozen=True)
class ExecutionRequest:
    signal_ts: int; decision_ts: int; side: str; reference_price: float; requested_qty: float; fee_rate: float
    best_bid: float | None = None; best_ask: float | None = None; spread_bps: float | None = None
    quote_source: str | None = None; quote_age_ms: int | None = None; execution_reality_level: str | None = None; top_of_book_source: str | None = None

@dataclass(frozen=True)
class ExecutionFill:
    fill_status: str; requested_qty: float; filled_qty: float; remaining_qty: float; avg_fill_price: float | None; fee: float
    latency_ms: int; model_name: str; model_version: str; model_params_hash: str; base_seed: int | None; derived_seed_hash: str; seed_derivation_inputs: dict[str, Any]

@dataclass
class StressExecutionModel:
    fee_rate: float; slippage_bps: float; latency_ms: int = 0; partial_fill_rate: float = 0.0; order_failure_rate: float = 0.0; market_order_extra_cost_bps: float = 0.0; seed: int | None = None; partial_fill_fraction: float = 0.5; seed_derivation_inputs: dict[str, Any] | None = None
    name: str = "stress"; version: str = "research_stress_v1"
    def params_payload(self) -> dict[str, Any]:
        return {"type": self.name, "version": self.version, "fee_rate": float(self.fee_rate), "slippage_bps": float(self.slippage_bps), "latency_ms": int(self.latency_ms), "partial_fill_rate": float(self.partial_fill_rate), "order_failure_rate": float(self.order_failure_rate), "market_order_extra_cost_bps": float(self.market_order_extra_cost_bps), "seed": self.seed, "partial_fill_fraction": float(self.partial_fill_fraction), "seed_derivation_inputs": self.seed_derivation_inputs}
    def simulate(self, request: ExecutionRequest) -> ExecutionFill:
        side = str(request.side).upper()
        if side not in {"BUY", "SELL"}: raise ValueError(f"unsupported execution side: {request.side}")
        params_hash = sha256_prefixed(self.params_payload())
        seeds = {"base_seed": self.seed, "model_params_hash": params_hash, "request": {"signal_ts": int(request.signal_ts), "decision_ts": int(request.decision_ts), "side": side, "order_type": "market", "reference_price": float(request.reference_price)}, **(self.seed_derivation_inputs or {})}
        seed_hash = sha256_prefixed(seeds)
        unit = lambda stream: int(sha256_hex({"seed_hash": seed_hash, "stream": stream})[:16], 16) / float(16**16)
        status, ratio = ("failed", 0.0) if unit("order_failure") < self.order_failure_rate else (("partial", min(max(self.partial_fill_fraction, 0.0), 1.0)) if unit("partial_fill") < self.partial_fill_rate else ("filled", 1.0))
        price = float(request.reference_price) * (1.0 + (1 if side == "BUY" else -1) * self.slippage_bps / 10_000.0)
        requested = max(0.0, float(request.requested_qty)); filled = requested * ratio
        return ExecutionFill(status, requested, filled, max(0.0, requested - filled), price if filled else None, filled * price * float(request.fee_rate), int(self.latency_ms), self.name, self.version, params_hash, self.seed, seed_hash, seeds)
