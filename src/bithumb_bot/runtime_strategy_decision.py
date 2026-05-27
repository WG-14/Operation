from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Protocol, runtime_checkable

from .config import settings, validate_live_strategy_selection
from .core.sma_policy import StrategyDecisionV2
from .runtime_position_state_normalizer import PositionStateNormalizer
from .runtime_sma_snapshot import decide_sma_with_filter_runtime_snapshot_from_db
from .runtime_sma_snapshot_builder import (
    RuntimeSmaDecisionResult,
    _latest_signal_close,
    _resolve_signal_through_ts_ms,
)
from .strategy import create_legacy_strategy, create_strategy_policy
from .strategy.sma_policy_strategy import SmaWithFilterStrategy


@runtime_checkable
class RuntimeStrategyDecisionResult(Protocol):
    decision: object
    base_context: dict[str, object]
    candle_ts: int
    market_price: float
    policy_hashes: object | None
    replay_fingerprint: dict[str, object]
    boundary: dict[str, object]

    def as_legacy_dict(self) -> dict[str, object]: ...


class RuntimeDecisionAdapter(Protocol):
    strategy_name: str

    def decide(
        self,
        conn,
        *,
        short_n: int,
        long_n: int,
        through_ts_ms: int | None = None,
    ) -> RuntimeStrategyDecisionResult | None: ...

    def typed_authority_required(self) -> bool: ...


RuntimeDecisionAdapterFactory = Callable[[], RuntimeDecisionAdapter]

_RUNTIME_DECISION_ADAPTERS: dict[str, RuntimeDecisionAdapterFactory] = {}


def _normalize_name(name: str) -> str:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("runtime strategy name must not be empty")
    return key


def register_runtime_decision_adapter(
    name: str,
    factory: RuntimeDecisionAdapterFactory,
) -> None:
    _RUNTIME_DECISION_ADAPTERS[_normalize_name(name)] = factory


def list_runtime_decision_adapters() -> tuple[str, ...]:
    return tuple(sorted(_RUNTIME_DECISION_ADAPTERS))


def get_runtime_decision_adapter(name: str) -> RuntimeDecisionAdapter | None:
    factory = _RUNTIME_DECISION_ADAPTERS.get(_normalize_name(name))
    return None if factory is None else factory()


def is_runtime_strategy_decision_result(value: object) -> bool:
    if not isinstance(value, RuntimeStrategyDecisionResult):
        return False
    return isinstance(getattr(value, "decision", None), StrategyDecisionV2)


def _normalization_boundary_label() -> str:
    return "engine.normalize_position_state_before_strategy_decision"


def normalize_position_state_before_strategy_decision(
    conn,
    strategy: SmaWithFilterStrategy,
    *,
    through_ts_ms: int | None = None,
    normalizer: PositionStateNormalizer | None = None,
) -> int:
    """Run explicit mutating position normalization before read-only decisions."""
    signal_through_ts_ms = _resolve_signal_through_ts_ms(
        interval=strategy.interval,
        through_ts_ms=through_ts_ms,
    )
    if signal_through_ts_ms is None:
        return 0
    market_price = _latest_signal_close(
        conn,
        pair=strategy.pair,
        interval=strategy.interval,
        through_ts_ms=signal_through_ts_ms,
    )
    if market_price is None:
        return 0
    return (normalizer or PositionStateNormalizer()).normalize_and_persist(
        conn,
        pair=strategy.pair,
        market_price=float(market_price),
        slippage_bps=float(strategy.slippage_bps),
        entry_edge_buffer_ratio=float(strategy.entry_edge_buffer_ratio),
    )


def normalize_position_state_for_runtime_decision(
    conn,
    strategy: SmaWithFilterStrategy,
    *,
    through_ts_ms: int | None = None,
    normalizer: PositionStateNormalizer | None = None,
) -> dict[str, object]:
    """Explicit mutable pre-decision phase for runtime strategy decisions."""
    updated_count = normalize_position_state_before_strategy_decision(
        conn,
        strategy,
        through_ts_ms=through_ts_ms,
        normalizer=normalizer,
    )
    return {
        "normalization_boundary": _normalization_boundary_label(),
        "normalization_updated_count": int(updated_count),
        "decision_boundary_phase": "pre_decision_normalization_complete",
    }


def build_read_only_strategy_decision_snapshot(
    conn,
    strategy: SmaWithFilterStrategy,
    *,
    through_ts_ms: int | None = None,
    boundary_telemetry: dict[str, object] | None = None,
) -> RuntimeSmaDecisionResult | None:
    """Post-normalization read-only decision phase.

    This function assumes any mutable position normalization has already
    completed. It must not call the normalizer or any legacy DB-bound strategy
    ``decide(conn)`` method.
    """
    result = decide_sma_with_filter_runtime_snapshot_from_db(
        conn,
        strategy,
        through_ts_ms=through_ts_ms,
    )
    if result is not None and boundary_telemetry:
        boundary = {**dict(result.boundary), **dict(boundary_telemetry)}
        boundary["decision_boundary_phase"] = "post_normalization_decision"
        result.base_context.update(boundary)
        object.__setattr__(result, "boundary", boundary)
    return result


def compute_strategy_decision_after_normalization(
    conn,
    strategy: SmaWithFilterStrategy,
    *,
    through_ts_ms: int | None = None,
    boundary_telemetry: dict[str, object] | None = None,
) -> RuntimeSmaDecisionResult | None:
    """Decision-only helper for callers that already ran normalization."""
    return build_read_only_strategy_decision_snapshot(
        conn,
        strategy,
        through_ts_ms=through_ts_ms,
        boundary_telemetry=boundary_telemetry,
    )


@dataclass(frozen=True)
class SmaWithFilterRuntimeDecisionAdapter:
    strategy_name: str = "sma_with_filter"

    def decide(
        self,
        conn,
        *,
        short_n: int,
        long_n: int,
        through_ts_ms: int | None = None,
    ) -> RuntimeSmaDecisionResult | None:
        strategy = create_strategy_policy(
            self.strategy_name,
            short_n=short_n,
            long_n=long_n,
            pair=settings.PAIR,
            interval=settings.INTERVAL,
        )
        if not isinstance(strategy, SmaWithFilterStrategy):
            raise RuntimeError(f"strategy_policy_invalid:{self.strategy_name}")
        boundary_telemetry = normalize_position_state_for_runtime_decision(
            conn,
            strategy,
            through_ts_ms=through_ts_ms,
        )
        return compute_strategy_decision_after_normalization(
            conn,
            strategy,
            through_ts_ms=through_ts_ms,
            boundary_telemetry=boundary_telemetry,
        )

    def typed_authority_required(self) -> bool:
        mode = str(settings.MODE or "").strip().lower()
        if mode == "live":
            return True
        if str(getattr(settings, "APPROVED_STRATEGY_PROFILE_PATH", "") or "").strip():
            return True
        return False


register_runtime_decision_adapter("sma_with_filter", SmaWithFilterRuntimeDecisionAdapter)


def promotion_grade_typed_runtime_decision_required(
    *,
    selected_strategy_name: str,
    compute_signal_fn: object | None = None,
    original_compute_signal_fn: object | None = None,
) -> bool:
    adapter = get_runtime_decision_adapter(selected_strategy_name)
    if adapter is None:
        return False
    if (
        compute_signal_fn is not None
        and original_compute_signal_fn is not None
        and compute_signal_fn is not original_compute_signal_fn
    ):
        return False
    return adapter.typed_authority_required()


def typed_runtime_handoff_failure_reason(
    signal_handoff: object,
    *,
    selected_strategy_name: str,
    compute_signal_fn: object | None = None,
    original_compute_signal_fn: object | None = None,
) -> str | None:
    if not promotion_grade_typed_runtime_decision_required(
        selected_strategy_name=selected_strategy_name,
        compute_signal_fn=compute_signal_fn,
        original_compute_signal_fn=original_compute_signal_fn,
    ):
        return None
    if is_runtime_strategy_decision_result(signal_handoff):
        return None
    return "typed_runtime_decision_required"


def legacy_db_strategy_fallback_allowed(*, selected_strategy_name: str) -> bool:
    if get_runtime_decision_adapter(selected_strategy_name) is not None:
        return False
    live_real_order = bool(
        str(settings.MODE).strip().lower() == "live"
        and bool(settings.LIVE_REAL_ORDER_ARMED)
        and not bool(settings.LIVE_DRY_RUN)
    )
    return not live_real_order


@dataclass(frozen=True)
class DecisionRunner:
    """Small orchestration seam for runtime strategy decisions."""

    strategy_name: str | None = None

    def decide_snapshot(
        self,
        conn,
        short_n: int,
        long_n: int,
        *,
        through_ts_ms: int | None = None,
        strategy_name: str | None = None,
    ) -> RuntimeStrategyDecisionResult | tuple[object, object] | None:
        return _compute_strategy_decision_snapshot_impl(
            conn,
            short_n,
            long_n,
            through_ts_ms=through_ts_ms,
            strategy_name=strategy_name or self.strategy_name,
        )


def compute_strategy_decision_snapshot(
    conn,
    short_n: int,
    long_n: int,
    *,
    through_ts_ms: int | None = None,
    strategy_name: str | None = None,
) -> RuntimeStrategyDecisionResult | tuple[object, object] | None:
    return DecisionRunner(strategy_name=strategy_name).decide_snapshot(
        conn,
        short_n,
        long_n,
        through_ts_ms=through_ts_ms,
    )


def _compute_strategy_decision_snapshot_impl(
    conn,
    short_n: int,
    long_n: int,
    *,
    through_ts_ms: int | None = None,
    strategy_name: str | None = None,
) -> RuntimeStrategyDecisionResult | tuple[object, object] | None:
    """Compute a strategy decision through a typed runtime adapter when available.

    The tuple return is an explicitly legacy DB-bound compatibility result for
    paper/smoke callers. Live real-order execution must not reach that branch.
    """
    selected_strategy_name = str(strategy_name or settings.STRATEGY_NAME).strip().lower()
    validate_live_strategy_selection(replace(settings, STRATEGY_NAME=selected_strategy_name))
    adapter = get_runtime_decision_adapter(selected_strategy_name)
    if adapter is not None:
        return adapter.decide(
            conn,
            short_n=short_n,
            long_n=long_n,
            through_ts_ms=through_ts_ms,
        )
    if not legacy_db_strategy_fallback_allowed(selected_strategy_name=selected_strategy_name):
        raise RuntimeError(f"legacy_db_strategy_not_allowed_for_live:{selected_strategy_name}")
    strategy = create_legacy_strategy(
        selected_strategy_name,
        short_n=short_n,
        long_n=long_n,
        pair=settings.PAIR,
        interval=settings.INTERVAL,
    )
    decision = strategy.decide(conn, through_ts_ms=through_ts_ms)
    return None if decision is None else (decision, strategy)


def compute_signal_runtime_handoff(
    conn,
    short_n: int,
    long_n: int,
    *,
    through_ts_ms: int | None = None,
    strategy_name: str | None = None,
) -> RuntimeStrategyDecisionResult | dict[str, object] | None:
    """Return typed runtime decisions before compatibility serialization."""
    result = compute_strategy_decision_snapshot(
        conn,
        short_n,
        long_n,
        through_ts_ms=through_ts_ms,
        strategy_name=strategy_name,
    )
    if result is None:
        return None
    if is_runtime_strategy_decision_result(result):
        return result
    decision, strategy = result
    payload = decision.as_dict()
    payload.setdefault("strategy", strategy.name)
    return payload


def compute_signal(
    conn,
    short_n: int,
    long_n: int,
    *,
    through_ts_ms: int | None = None,
    strategy_name: str | None = None,
):
    result = compute_signal_runtime_handoff(
        conn,
        short_n,
        long_n,
        through_ts_ms=through_ts_ms,
        strategy_name=strategy_name,
    )
    if result is None:
        return None
    if is_runtime_strategy_decision_result(result):
        payload = result.as_legacy_dict()
        payload.setdefault("strategy", result.decision.strategy_name)
        return payload
    return result


ORIGINAL_COMPUTE_SIGNAL = compute_signal
