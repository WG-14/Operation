from __future__ import annotations

import pytest

from bithumb_bot.h74_observation import H74ObservationAuthorityError
from bithumb_bot.h74_restore_check import verify_h74_restore_original_window
from tests.test_h74_authority_env_alignment import _settings
from tests.test_h74_source_variant_authority import _source, _variant


def test_restore_check_passes_for_source_authority_and_9_11_env() -> None:
    result = verify_h74_restore_original_window(
        authority_payload=_source(),
        settings_obj=_settings(9, 11),
        env_hash="sha256:" + "1" * 64,
    )
    assert result["status"] == "PASS"
    assert result["source_authority_hash"].startswith("sha256:")
    assert result["effective_behavior_parameter_hash"].startswith("sha256:")


def test_restore_check_rejects_no_window_authority_path() -> None:
    with pytest.raises(H74ObservationAuthorityError, match="requires_source_authority"):
        verify_h74_restore_original_window(
            authority_payload=_variant(),
            settings_obj=_settings(0, 24),
            env_hash="sha256:" + "1" * 64,
        )


def test_restore_check_rejects_env_0_24() -> None:
    with pytest.raises(H74ObservationAuthorityError):
        verify_h74_restore_original_window(
            authority_payload=_source(),
            settings_obj=_settings(0, 24),
            env_hash="sha256:" + "1" * 64,
        )


def test_restore_check_rejects_non_window_behavior_mismatch() -> None:
    cfg = _settings(9, 11)
    cfg.SMA_LONG = 99
    with pytest.raises(H74ObservationAuthorityError, match="SMA_LONG|runtime_mismatch"):
        verify_h74_restore_original_window(
            authority_payload=_source(),
            settings_obj=cfg,
            env_hash="sha256:" + "1" * 64,
        )
