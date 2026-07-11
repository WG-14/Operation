from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bithumb_bot.date_range import DateRange


def test_operation_date_range_start_timestamp_is_utc_midnight() -> None:
    date_range = DateRange(start="2024-01-01", end="2024-01-01")

    assert date_range.start_ts_ms() == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def test_operation_date_range_end_timestamp_is_inclusive() -> None:
    date_range = DateRange(start="2024-01-01", end="2024-01-01")

    assert date_range.end_ts_ms() == date_range.start_ts_ms() + 86_400_000 - 1


def test_operation_date_range_uses_utc_not_local_timezone() -> None:
    date_range = DateRange(start="2024-01-01", end="2024-01-01")

    assert date_range.start_ts_ms() == 1_704_067_200_000


def test_operation_date_range_accepts_leap_day() -> None:
    date_range = DateRange(start="2024-02-29", end="2024-02-29")

    assert date_range.start_ts_ms() == int(datetime(2024, 2, 29, tzinfo=UTC).timestamp() * 1000)


def test_operation_date_range_rejects_nonexistent_date() -> None:
    with pytest.raises(ValueError, match=r"invalid date '2024-02-30'; expected YYYY-MM-DD"):
        DateRange(start="2024-02-30", end="2024-02-30").start_ts_ms()


def test_operation_date_range_rejects_invalid_format() -> None:
    with pytest.raises(ValueError, match=r"invalid date '2024/02/29'; expected YYYY-MM-DD"):
        DateRange(start="2024/02/29", end="2024/02/29").start_ts_ms()


def test_operation_date_range_as_dict_preserves_boundaries() -> None:
    assert DateRange(start="2024-02-29", end="2024-03-01").as_dict() == {
        "start": "2024-02-29",
        "end": "2024-03-01",
    }
