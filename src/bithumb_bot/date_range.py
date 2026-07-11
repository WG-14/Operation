from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DateRange:
    """Inclusive UTC date range used by operation-owned date consumers."""

    start: str
    end: str

    def start_ts_ms(self) -> int:
        return _date_start_ts_ms(self.start)

    def end_ts_ms(self) -> int:
        return _date_end_ts_ms(self.end)

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end}


def _date_start_ts_ms(value: str) -> int:
    return int(_parse_date(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _date_end_ts_ms(value: str) -> int:
    return _date_start_ts_ms(value) + 86_400_000 - 1


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc
