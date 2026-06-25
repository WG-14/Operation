from __future__ import annotations

import pytest

from bithumb_bot.h74_observation import H74ObservationAuthorityError
from tests.test_h74_source_variant_authority import _source, _envelope
from bithumb_bot.h74_observation import build_h74_source_variant_observation_authority_payload


def _build(overrides: dict[str, object]) -> dict[str, object]:
    return build_h74_source_variant_observation_authority_payload(
        base_authority=_source(),
        variant_overrides=overrides,
        experiment_envelope_payload=_envelope(),
    )


def test_no_window_variant_allows_only_entry_window_keys() -> None:
    payload = _build(
        {
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0,
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
        }
    )
    assert sorted(payload["variant_overrides"]) == [
        "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST",
        "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST",
    ]


@pytest.mark.parametrize("key", ["SMA_SHORT", "STRATEGY_EXIT_MAX_HOLDING_MIN", "DAILY_PARTICIPATION_MAX_ORDER_KRW"])
def test_no_window_variant_rejects_forbidden_override(key: str) -> None:
    with pytest.raises(H74ObservationAuthorityError, match="forbidden_variant_override"):
        _build(
            {
                "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0,
                "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
                key: 9,
            }
        )


def test_no_window_variant_rejects_sma_short_override() -> None:
    test_no_window_variant_rejects_forbidden_override("SMA_SHORT")


def test_no_window_variant_rejects_exit_holding_override() -> None:
    test_no_window_variant_rejects_forbidden_override("STRATEGY_EXIT_MAX_HOLDING_MIN")


def test_no_window_variant_rejects_order_size_override() -> None:
    test_no_window_variant_rejects_forbidden_override("DAILY_PARTICIPATION_MAX_ORDER_KRW")


def test_no_window_variant_records_base_and_variant_parameter_hashes() -> None:
    payload = _build(
        {
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0,
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
        }
    )
    assert payload["base_behavior_parameter_hash"].startswith("sha256:")
    assert payload["variant_behavior_parameter_hash"].startswith("sha256:")
    assert payload["base_behavior_parameter_hash"] != payload["variant_behavior_parameter_hash"]
