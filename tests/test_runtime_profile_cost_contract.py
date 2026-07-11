from __future__ import annotations

from bithumb_bot.operation_approval import profile_runtime_cost_match_status


def _approval(*, max_order_krw: float = 50_000.0) -> dict[str, object]:
    return {
        "max_order_krw": max_order_krw,
    }


def _runtime(*, max_order_krw: float = 50_000.0) -> dict[str, object]:
    return {
        "mode": "live",
        "max_order_krw": max_order_krw,
    }


def test_operation_approval_rejects_runtime_order_limit_above_approval() -> None:
    result = profile_runtime_cost_match_status(_approval(max_order_krw=50_000.0), _runtime(max_order_krw=50_001.0))

    assert result["status"] == "FAIL"
    assert result["reason"] == "runtime_max_order_exceeds_operation_approval"
    assert result["expected"] == 50_000.0
    assert result["actual"] == 50_001.0


def test_operation_approval_allows_runtime_order_limit_at_or_below_approval() -> None:
    result = profile_runtime_cost_match_status(_approval(max_order_krw=50_000.0), _runtime(max_order_krw=50_000.0))

    assert result["status"] == "PASS"
    assert result["reason"] == "operation_approval_runtime_limits_match"


def test_missing_operation_approval_is_reported_as_warning() -> None:
    result = profile_runtime_cost_match_status(None, _runtime())

    assert result == {"status": "WARN", "reason": "operation_approval_not_loaded"}
