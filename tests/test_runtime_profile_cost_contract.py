from __future__ import annotations

from bithumb_bot.approved_profile import PROFILE_RUNTIME_COST_MISMATCH_ACTION, profile_runtime_cost_match_status


def _profile(*, fee_rate: float = 0.0004, slippage_bps: float = 10.0, role: str = "base") -> dict[str, object]:
    return {
        "base_cost_assumption": {
            "label": "profile_base_cost",
            "role": role,
            "fee_rate": fee_rate,
            "fee_source": "operator_declared_bithumb_app_fee",
            "fee_authority_policy": "runtime_fee_authority_must_match_or_fail",
            "slippage_bps": slippage_bps,
            "slippage_source": "execution_calibration",
            "promotable_as_base": role == "base",
        }
    }


def _runtime(*, fee_rate: float = 0.0004, slippage_bps: float = 10.0, degraded: bool = False) -> dict[str, object]:
    return {
        "mode": "live",
        "cost_model": {
            "fee_rate": fee_rate,
            "slippage_bps": slippage_bps,
            "fee_authority_degraded": degraded,
        },
    }


def test_profile_fee_0025_runtime_fee_0004_fails_live_cost_match() -> None:
    result = profile_runtime_cost_match_status(_profile(fee_rate=0.0025), _runtime(fee_rate=0.0004))

    assert result["status"] == "FAIL"
    assert result["reason"] == "runtime_profile_cost_mismatch"
    assert result["operator_next_step"] == PROFILE_RUNTIME_COST_MISMATCH_ACTION


def test_profile_fee_0004_runtime_degraded_fallback_warns() -> None:
    result = profile_runtime_cost_match_status(_profile(fee_rate=0.0004), _runtime(fee_rate=0.0004, degraded=True))

    assert result["status"] == "WARN"
    assert result["reason"] == "runtime_fee_authority_degraded"


def test_runtime_degraded_to_0025_with_profile_0004_fails() -> None:
    result = profile_runtime_cost_match_status(
        _profile(fee_rate=0.0004),
        _runtime(fee_rate=0.0025, degraded=True),
    )

    assert result["status"] == "FAIL"
    assert result["reason"] == "runtime_profile_cost_mismatch"


def test_stress_cost_is_not_accepted_as_runtime_base_cost() -> None:
    result = profile_runtime_cost_match_status(_profile(fee_rate=0.0025, role="stress"), _runtime(fee_rate=0.0025))

    assert result["status"] == "FAIL"
    assert result["reason"] == "stress_cost_is_not_runtime_base_cost"
    assert "Regenerate or select an approved profile" in result["operator_next_step"]
