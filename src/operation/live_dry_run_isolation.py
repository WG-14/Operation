from __future__ import annotations

from pathlib import Path


class LiveDryRunIsolationError(RuntimeError):
    pass


def live_dry_run_uses_direct_live_sqlite(settings_obj: object) -> bool:
    mode = str(getattr(settings_obj, "MODE", "") or "").strip().lower()
    if mode != "live" or not bool(getattr(settings_obj, "LIVE_DRY_RUN", False)):
        return False
    db_path = Path(str(getattr(settings_obj, "DB_PATH", "") or "")).expanduser()
    return db_path.name == "live.sqlite" and "trades" in {part.lower() for part in db_path.parts}


def validate_live_dry_run_state_isolation(settings_obj: object) -> None:
    if live_dry_run_uses_direct_live_sqlite(settings_obj):
        raise LiveDryRunIsolationError(
            "live_dry_run_refuses_direct_live_sqlite_without_copy_or_namespace; "
            "use an isolated DB copy under DATA_ROOT/live/reports or a documented no-persist path"
        )
