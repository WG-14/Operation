from __future__ import annotations

from dataclasses import dataclass

from ..live_pipeline_smoke_authority import (
    LIVE_PIPELINE_SMOKE_CYCLES,
    LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW,
    LIVE_PIPELINE_SMOKE_MAX_ORDERS,
)
from ..live_pipeline_smoke_preflight import LivePipelineSmokeReadiness


OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME = "operator_live_pipeline_smoke"

SMOKE_DECISION_CONTEXT = {
    "strategy_name": OPERATOR_LIVE_PIPELINE_SMOKE_STRATEGY_NAME,
    "strategy_performance_gate_scope": "not_applicable_operator_authorized_pipeline_smoke",
    "strategy_performance_gate_enforced": False,
    "promotion_evidence": False,
    "approved_profile_evidence": False,
    "normal_strategy_gate_modified": False,
}


class LivePipelineSmokeDecisionError(ValueError):
    pass


@dataclass
class LivePipelineSmokeDecisionProvider:
    run_id: str
    cycles: int = LIVE_PIPELINE_SMOKE_CYCLES
    max_orders: int = LIVE_PIPELINE_SMOKE_MAX_ORDERS
    max_notional_krw: float = LIVE_PIPELINE_SMOKE_DEFAULT_MAX_NOTIONAL_KRW
    step_index: int = 0
    live_mode: bool = True

    def __post_init__(self) -> None:
        if int(self.max_orders) != int(self.cycles) * 2:
            raise LivePipelineSmokeDecisionError("live_pipeline_smoke_max_orders_must_equal_cycles_x2")
        if bool(self.live_mode) and int(self.max_orders) > LIVE_PIPELINE_SMOKE_MAX_ORDERS:
            raise LivePipelineSmokeDecisionError("live_pipeline_smoke_live_max_orders_above_10")
        if bool(self.live_mode) and int(self.cycles) != LIVE_PIPELINE_SMOKE_CYCLES:
            raise LivePipelineSmokeDecisionError("live_pipeline_smoke_live_cycles_must_be_5")

    def next_side(self, readiness: LivePipelineSmokeReadiness) -> str:
        if self.step_index >= int(self.cycles) * 2:
            return "STOP"
        if self.step_index % 2 == 0:
            if not readiness.flat:
                raise LivePipelineSmokeDecisionError("live_pipeline_smoke_buy_requires_flat")
            return "BUY"
        if not readiness.in_position:
            raise LivePipelineSmokeDecisionError("live_pipeline_smoke_sell_requires_position")
        return "SELL"

    def target_exposure_krw_for_side(self, side: str) -> float:
        normalized = str(side or "").upper()
        if normalized == "BUY":
            return float(self.max_notional_krw)
        if normalized == "SELL":
            return 0.0
        raise LivePipelineSmokeDecisionError("live_pipeline_smoke_unknown_side")

    def context_for_step(self, *, side: str) -> dict[str, object]:
        return {
            **SMOKE_DECISION_CONTEXT,
            "run_id": self.run_id,
            "step_index": int(self.step_index),
            "round": int(self.step_index // 2) + 1,
            "side": str(side).upper(),
            "target_exposure_krw": self.target_exposure_krw_for_side(side),
            "execution_mode": "live_pipeline_smoke",
            "candle_checkpoint_authority": "smoke_step_checkpoint",
            "market_reference_source": "latest_closed_candle_or_top_of_book",
        }

    def mark_step_complete(self) -> None:
        self.step_index += 1
