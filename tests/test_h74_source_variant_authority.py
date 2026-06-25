from __future__ import annotations

import pytest

from bithumb_bot.h74_observation import (
    H74ObservationAuthorityError,
    H74_SOURCE_OBSERVATION_PARAMETERS,
    H74_SOURCE_VARIANT_OBSERVATION_AUTHORITY_ARTIFACT_TYPE,
    build_h74_observation_experiment_envelope,
    build_h74_source_observation_authority_payload,
    build_h74_source_variant_observation_authority_payload,
    verify_h74_source_observation_authority,
    verify_h74_source_variant_observation_authority,
)


def _envelope() -> dict[str, object]:
    return build_h74_observation_experiment_envelope(
        experiment_run_id="exp",
        runtime_git_commit_sha="commit",
        runtime_git_clean=True,
        env_hash="sha256:" + "1" * 64,
        strategy_revision_id="sha256:" + "2" * 64,
        risk_scope_id="sha256:" + "3" * 64,
        risk_baseline_certificate_hash="sha256:" + "4" * 64,
        starting_broker_position={"qty": 0},
        starting_local_position={"qty": 0},
        db_snapshot_hash="sha256:" + "5" * 64,
        included_history_policy="declared_live_history_scope",
    )


def _source() -> dict[str, object]:
    return build_h74_source_observation_authority_payload(
        source_candidate_artifact_hash="sha256:source",
        backtest_report_hash="sha256:backtest",
        validation_run_hash="sha256:validation",
        code_commit_sha="commit",
        experiment_envelope_payload=_envelope(),
    )


def _variant() -> dict[str, object]:
    return build_h74_source_variant_observation_authority_payload(
        base_authority=_source(),
        variant_overrides={
            "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0,
            "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
        },
        experiment_envelope_payload=_envelope(),
    )


def test_no_window_variant_authority_has_distinct_authority_type() -> None:
    payload = _variant()
    assert payload["authority_type"] == H74_SOURCE_VARIANT_OBSERVATION_AUTHORITY_ARTIFACT_TYPE
    with pytest.raises(H74ObservationAuthorityError, match="artifact_type_invalid"):
        verify_h74_source_observation_authority(payload, runtime_values=H74_SOURCE_OBSERVATION_PARAMETERS)


def test_no_window_variant_authority_binds_base_candidate_and_variant_id() -> None:
    payload = _variant()
    assert payload["base_candidate_id"] == "candidate_9738b8d6"
    assert payload["variant_id"]
    assert payload["base_source_authority_hash"]
    assert payload["equivalence_to_source_candidate"] is False
    assert payload["production_approval"] is False


def test_no_window_variant_authority_hash_bound_window_is_0_to_24() -> None:
    payload = _variant()
    runtime = dict(H74_SOURCE_OBSERVATION_PARAMETERS)
    runtime["DAILY_PARTICIPATION_WINDOW_START_HOUR_KST"] = 0
    runtime["DAILY_PARTICIPATION_WINDOW_END_HOUR_KST"] = 24
    verify_h74_source_variant_observation_authority(payload, runtime_values=runtime)
    assert payload["hash_bound_parameters"]["DAILY_PARTICIPATION_WINDOW_START_HOUR_KST"] == 0
    assert payload["hash_bound_parameters"]["DAILY_PARTICIPATION_WINDOW_END_HOUR_KST"] == 24


def test_source_authority_remains_9_to_11() -> None:
    payload = _source()
    verify_h74_source_observation_authority(payload, runtime_values=H74_SOURCE_OBSERVATION_PARAMETERS)
    assert payload["hash_bound_parameters"]["DAILY_PARTICIPATION_WINDOW_START_HOUR_KST"] == 9
    assert payload["hash_bound_parameters"]["DAILY_PARTICIPATION_WINDOW_END_HOUR_KST"] == 11
