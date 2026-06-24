from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field, make_dataclass, replace
from typing import Callable, Mapping

from .config import settings
from .h74_observation import H74_SOURCE_OBSERVATION_PARAMETERS, H74_STRATEGY_NAME


Clock = Callable[[], float]
DbFactory = Callable[..., sqlite3.Connection]


@dataclass(frozen=True)
class H74LiveRehearsalContext:
    settings_snapshot: object
    clock: Clock
    db_factory: DbFactory | None = None
    broker_snapshot_provider: Callable[..., object] | None = None
    order_rules_provider: Callable[[], Mapping[str, object]] | None = None
    feature_snapshot_provider: Callable[..., object] | None = None
    environment_overrides: Mapping[str, object] | None = None


def default_h74_live_rehearsal_context() -> H74LiveRehearsalContext:
    snapshot_values = dict(vars(settings))
    snapshot_values.update(
        {
            "MODE": "live",
            "LIVE_DRY_RUN": False,
            "LIVE_REAL_ORDER_ARMED": True,
            "EXECUTION_ENGINE": "target_delta",
            "MAX_DAILY_LOSS_KRW": 0.0,
            "MAX_DAILY_ORDER_COUNT": 0,
            "STRATEGY_NAME": H74_STRATEGY_NAME,
            "PAIR": "KRW-BTC",
            "INTERVAL": "1m",
            "TARGET_EXPOSURE_KRW": 100_000.0,
            "MAX_ORDER_KRW": 100_000.0,
            "MIN_ORDER_NOTIONAL_KRW": 5000.0,
            "LIVE_MIN_ORDER_QTY": 0.0001,
            "LIVE_ORDER_QTY_STEP": 0.0001,
            "LIVE_ORDER_MAX_QTY_DECIMALS": 8,
            "H74_LIVE_REHEARSAL_NO_SUBMIT_BOUNDARY": True,
            "DAILY_PARTICIPATION_MAX_DAILY_ENTRY_COUNT": H74_SOURCE_OBSERVATION_PARAMETERS[
                "max_daily_entry_count"
            ],
        }
    )
    for key, value in H74_SOURCE_OBSERVATION_PARAMETERS.items():
        if str(key).isupper():
            snapshot_values[str(key)] = value
    snapshot_cls = make_dataclass(
        "H74SettingsSnapshot",
        [(key, object, field(default=value)) for key, value in snapshot_values.items()],
    )
    return H74LiveRehearsalContext(
        settings_snapshot=snapshot_cls(),
        clock=time.time,
        environment_overrides={"H74_LIVE_REHEARSAL_NO_SUBMIT_BOUNDARY": True},
    )


def with_h74_source_authority_path(context: H74LiveRehearsalContext, authority_path: str) -> H74LiveRehearsalContext:
    return replace(
        context,
        settings_snapshot=replace(
            context.settings_snapshot,
            H74_SOURCE_OBSERVATION_AUTHORITY_PATH=str(authority_path),
        ),
    )


__all__ = [
    "H74LiveRehearsalContext",
    "default_h74_live_rehearsal_context",
    "with_h74_source_authority_path",
]
