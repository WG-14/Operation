from __future__ import annotations

from pathlib import Path

import pytest

from bithumb_bot.h74_pre_submit_evidence import (
    H74PreSubmitEvidenceError,
    build_h74_pre_submit_evidence_bundle,
    require_pre_submit_bundle_hash,
)
from tests.test_h74_authority_env_alignment import _settings
from tests.test_h74_source_variant_authority import _source, _variant


def _bundle(authority: dict[str, object], *, start: int = 0, end: int = 24, flat: bool = True, min_free: int = 1) -> dict[str, object]:
    return build_h74_pre_submit_evidence_bundle(
        authority_payload=authority,
        settings_obj=_settings(start, end),
        env_hash="sha256:" + "6" * 64,
        risk_baseline_certificate_hash="sha256:" + "7" * 64,
        db_snapshot_hash="sha256:" + "8" * 64,
        starting_broker_position={"qty": 0},
        starting_local_position={"qty": 0},
        flat_start_proof={"flat": flat},
        disk_capacity_path="/tmp",
        min_free_bytes=min_free,
    )


def test_pre_submit_bundle_requires_authority_env_match() -> None:
    with pytest.raises(Exception, match="MISMATCH|runtime_mismatch"):
        _bundle(_source(), start=0, end=24)


def test_pre_submit_bundle_requires_flat_start() -> None:
    with pytest.raises(H74PreSubmitEvidenceError, match="flat_start_required"):
        _bundle(_variant(), flat=False)


def test_pre_submit_bundle_requires_disk_capacity() -> None:
    with pytest.raises(H74PreSubmitEvidenceError, match="disk_capacity_insufficient"):
        _bundle(_variant(), min_free=10**30)


def test_pre_submit_bundle_records_effective_behavior_parameters() -> None:
    payload = _bundle(_variant())
    assert payload["effective_behavior_parameters"]["DAILY_PARTICIPATION_WINDOW_START_HOUR_KST"] == 0
    assert payload["variant_overrides"]["DAILY_PARTICIPATION_WINDOW_END_HOUR_KST"] == 24
    assert payload["pre_submit_evidence_hash"].startswith("sha256:")


def test_probe_run_requires_pre_submit_bundle_hash() -> None:
    with pytest.raises(H74PreSubmitEvidenceError, match="required"):
        require_pre_submit_bundle_hash({})
    require_pre_submit_bundle_hash(_bundle(_variant()))
