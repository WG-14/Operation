"""Exchange-neutral local operator commands."""

from __future__ import annotations

import json

from .config import settings
from .runtime_state import disable_trading_until


def cmd_config_dump(*, masked: bool = False) -> None:
    payload = {
        "mode": settings.MODE,
        "pair": settings.PAIR,
        "interval": settings.INTERVAL,
        "db_path": settings.DB_PATH,
        "live_broker": "not_configured",
        "reason_code": "LIVE_BROKER_NOT_CONFIGURED",
    }
    del masked
    print(json.dumps(payload, sort_keys=True))


def cmd_pause() -> None:
    disable_trading_until(float("inf"), reason="operator pause")
    print("[PAUSE] trading paused")


def cmd_status() -> None:
    print("[STATUS] live_broker=not_configured reason_code=LIVE_BROKER_NOT_CONFIGURED")
