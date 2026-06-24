from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bithumb_bot.core.sma_policy import _stable_hash
from bithumb_bot.strategy.daily_participation_events import (
    ParticipationEvent,
    participation_event_set_hash,
    source_contract_hash,
)
from bithumb_bot.strategy.daily_participation_policy import (
    DailyParticipationCountSnapshot,
    DailyParticipationPolicyConfig,
    TIMESTAMP_FIELD_BY_BASIS,
    kst_day,
)


@dataclass(frozen=True)
class DailyParticipationReducer:
    query_contract: str
    source: str
    source_contract_version: str

    def reduce(
        self,
        *,
        config: DailyParticipationPolicyConfig,
        decision_ts: int,
        events: Iterable[ParticipationEvent],
        pair: str,
        strategy_instance_id: str,
        strategy_name: str,
        pending_claim_count: int = 0,
    ) -> DailyParticipationCountSnapshot:
        scope_instance = str(strategy_instance_id or "").strip()
        if not scope_instance:
            raise ValueError("daily_participation_strategy_instance_scope_missing")
        day = kst_day(decision_ts, config.timezone)
        canonical_events = tuple(
            event
            for event in events
            if event.event_ts < int(decision_ts) and kst_day(event.event_ts, config.timezone) == day
        )
        rows = tuple(_event_row(event) for event in canonical_events)
        return DailyParticipationCountSnapshot(
            count_basis=config.count_basis,
            timezone=config.timezone,
            kst_day=day,
            count_for_kst_day=len(canonical_events),
            timestamp_field=TIMESTAMP_FIELD_BY_BASIS[config.count_basis],
            source=self.source,
            rows=rows,
            pair=pair,
            strategy_instance_id=scope_instance,
            event_set_hash=participation_event_set_hash(canonical_events),
            source_contract_hash=source_contract_hash(
                source=self.source,
                source_contract_version=self.source_contract_version,
            ),
            query_contract_hash=_stable_hash(
                {
                    "schema_version": 1,
                    "query_contract": self.query_contract,
                    "count_basis": config.count_basis,
                    "pair": pair,
                    "strategy_instance_id": scope_instance,
                    "strategy_name": strategy_name,
                    "kst_day": day,
                    "pending_claim_count": int(pending_claim_count),
                }
            ),
            source_contract_version=self.source_contract_version,
            pending_claim_count=int(pending_claim_count),
        )


def _event_row(event: ParticipationEvent) -> dict[str, object]:
    payload = event.as_dict()
    payload["basis"] = event.count_basis
    payload["ts"] = int(event.event_ts)
    return payload


__all__ = ["DailyParticipationReducer"]
