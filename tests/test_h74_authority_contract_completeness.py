from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bithumb_bot.decision_equivalence import sha256_prefixed
from bithumb_bot.h74_authority_alignment import validate_h74_authority_env_alignment
from bithumb_bot.h74_observation import (
    H74ObservationAuthorityError,
    H74_SOURCE_OBSERVATION_PARAMETERS,
    build_h74_observation_experiment_envelope,
    build_h74_source_observation_authority_payload,
    build_h74_source_variant_observation_authority_payload,
)
from bithumb_bot.experiment_execution_contract import POSITION_MODE_FIXED_FILL_QTY_UNTIL_EXIT


def _envelope() -> dict[str, object]:
    return build_h74_observation_experiment_envelope(
        experiment_run_id="probe-exp", runtime_git_commit_sha="commit", runtime_git_clean=True,
        env_hash="sha256:" + "1" * 64, strategy_revision_id="sha256:" + "2" * 64,
        risk_scope_id="sha256:" + "3" * 64, risk_baseline_certificate_hash="sha256:" + "4" * 64,
        starting_broker_position={"qty": 0}, starting_local_position={"qty": 0},
        db_snapshot_hash="sha256:" + "5" * 64, included_history_policy="declared_live_history_scope",
    )


def _variant_authority() -> dict[str, object]:
    source = build_h74_source_observation_authority_payload(
        source_candidate_artifact_hash="sha256:source", backtest_report_hash="sha256:backtest",
        validation_run_hash="sha256:validation", code_commit_sha="commit",
        experiment_envelope_payload=_envelope(),
    )
    payload = build_h74_source_variant_observation_authority_payload(
        base_authority=source,
        variant_overrides={"DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0, "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24},
        experiment_envelope_payload=_envelope(),
    )
    bound = dict(payload["hash_bound_parameters"])
    bound["H74_EXECUTION_PATH_PROBE_RUN_ID"] = "probe-run-1"
    payload["hash_bound_parameters"] = bound
    payload["probe_run_id"] = "probe-run-1"
    return _rehash(payload)


def _write_authority(tmp_path, payload: dict[str, object]) -> str:
    path = tmp_path / "h74-authority.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return str(path)


def _settings(authority_path: str, **overrides: object) -> SimpleNamespace:
    values = dict(H74_SOURCE_OBSERVATION_PARAMETERS)
    values.update({
        "MODE": "live", "LIVE_DRY_RUN": False, "LIVE_REAL_ORDER_ARMED": True,
        "EXECUTION_ENGINE": "target_delta", "STRATEGY_NAME": "daily_participation_sma",
        "PAIR": "KRW-BTC", "INTERVAL": "1m", "MAX_ORDER_KRW": 100_000.0,
        "MAX_DAILY_ORDER_COUNT": 2, "H74_SOURCE_OBSERVATION_AUTHORITY_PATH": authority_path,
        "H74_EXECUTION_PATH_PROBE_RUN_ID": "probe-run-1",
        "H74_EXECUTION_PATH_PROBE_PRE_SUBMIT_EVIDENCE_PATH": "", "H74_READINESS_CERTIFICATE_PATH": "",
        "POSITION_MODE": POSITION_MODE_FIXED_FILL_QTY_UNTIL_EXIT, "TARGET_EXPOSURE_KRW": None,
        "TARGET_HOLD_POLICY": "maintain_previous_target", "TARGET_EXECUTION_SHADOW": False,
        "LIVE_ORDER_MAX_QTY_DECIMALS": 8, "MIN_NET_EDGE_KRW": 0.0,
        "MIN_MARGIN_AFTER_COST_RATIO": 0.0, "PRE_TRADE_ECONOMICS_BLOCKING_ENABLED": False,
        "DAILY_PARTICIPATION_WINDOW_START_HOUR_KST": 0, "DAILY_PARTICIPATION_WINDOW_END_HOUR_KST": 24,
    })
    values.update(overrides)
    return SimpleNamespace(**values)


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["authority_content_hash"] = sha256_prefixed(
        {key: value for key, value in payload.items() if key != "authority_content_hash"}
    )
    return payload


def test_h74_authority_contract_missing_strategy_instance_id_blocks_live_probe(tmp_path) -> None:
    authority = _variant_authority()
    authority.pop("strategy_instance_id", None)
    bound = dict(authority["hash_bound_parameters"])
    bound.pop("strategy_instance_id", None)
    authority["hash_bound_parameters"] = bound
    cfg = _settings(_write_authority(tmp_path, authority))

    with pytest.raises(H74ObservationAuthorityError, match="h74_authority_contract_incomplete:strategy_instance_id"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


def test_h74_authority_contract_missing_partial_fill_policy_blocks_live_probe(tmp_path) -> None:
    authority = _variant_authority()
    authority.pop("partial_fill_policy", None)
    bound = dict(authority["hash_bound_parameters"])
    bound.pop("partial_fill_policy", None)
    authority["hash_bound_parameters"] = bound
    cfg = _settings(_write_authority(tmp_path, authority))

    with pytest.raises(H74ObservationAuthorityError, match="h74_authority_contract_incomplete:partial_fill_policy"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


@pytest.mark.parametrize(
    ("top_field", "bound_field", "reason_field"),
    [
        ("position_mode", "position_mode", "position_mode"),
        ("hold_policy", "hold_policy", "hold_policy"),
        ("authority_content_hash", "authority_content_hash", "authority_content_hash"),
    ],
)
def test_h74_authority_contract_missing_required_fixed_position_field_blocks_live_probe(
    tmp_path,
    top_field: str,
    bound_field: str,
    reason_field: str,
) -> None:
    authority = _variant_authority()
    authority.pop(top_field, None)
    bound = dict(authority["hash_bound_parameters"])
    bound.pop(bound_field, None)
    authority["hash_bound_parameters"] = bound
    cfg = _settings(_write_authority(tmp_path, authority))

    with pytest.raises(H74ObservationAuthorityError, match=f"h74_authority_contract_incomplete:{reason_field}"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


def test_h74_authority_contract_max_order_mismatch_blocks_live_probe(tmp_path) -> None:
    authority = _variant_authority()
    cfg = _settings(
        _write_authority(tmp_path, authority),
        MAX_ORDER_KRW=99_000.0,
        DAILY_PARTICIPATION_MAX_ORDER_KRW=99_000.0,
    )

    with pytest.raises(H74ObservationAuthorityError, match="h74_authority_contract_mismatch:max_order_krw"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


def test_h74_authority_contract_missing_probe_run_id_blocks_live_probe(tmp_path) -> None:
    authority = _variant_authority()
    authority.pop("probe_run_id", None)
    bound = dict(authority["hash_bound_parameters"])
    bound.pop("probe_run_id", None)
    bound.pop("H74_EXECUTION_PATH_PROBE_RUN_ID", None)
    authority["hash_bound_parameters"] = bound
    authority = _rehash(authority)
    cfg = _settings(_write_authority(tmp_path, authority))

    with pytest.raises(H74ObservationAuthorityError, match="h74_authority_contract_incomplete:probe_run_id"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


def test_h74_authority_contract_probe_run_id_mismatch_blocks_live_probe(tmp_path) -> None:
    authority = _variant_authority()
    authority["probe_run_id"] = "probe-a"
    bound = dict(authority["hash_bound_parameters"])
    bound["H74_EXECUTION_PATH_PROBE_RUN_ID"] = "probe-a"
    authority["hash_bound_parameters"] = bound
    authority = _rehash(authority)
    cfg = _settings(_write_authority(tmp_path, authority), H74_EXECUTION_PATH_PROBE_RUN_ID="probe-b")

    with pytest.raises(H74ObservationAuthorityError, match="h74_authority_contract_mismatch:probe_run_id"):
        validate_h74_authority_env_alignment(authority, settings_obj=cfg)


def test_h74_authority_contract_contains_required_fixed_position_fields(tmp_path) -> None:
    authority = _variant_authority()
    cfg = _settings(_write_authority(tmp_path, authority))

    result = validate_h74_authority_env_alignment(authority, settings_obj=cfg)

    assert result.ok is True
    for field in ("strategy_instance_id", "position_mode", "hold_policy", "partial_fill_policy", "authority_content_hash", "probe_run_id"):
        assert authority[field]
    assert authority["hash_bound_parameters"]["DAILY_PARTICIPATION_MAX_ORDER_KRW"] == pytest.approx(100_000.0)
    assert authority["hash_bound_parameters"]["H74_EXECUTION_PATH_PROBE_RUN_ID"] == "probe-run-1"
    assert cfg.H74_EXECUTION_PATH_PROBE_RUN_ID == "probe-run-1"
