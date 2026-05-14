from __future__ import annotations

import pytest

from bithumb_bot.research.experiment_manifest import ManifestValidationError, parse_manifest


def _manifest(*, deployment_tier: str = "paper_candidate") -> dict[str, object]:
    return {
        "experiment_id": "cost_contract_test",
        "hypothesis": "Cost assumptions are explicit.",
        "strategy_name": "sma_with_filter",
        "deployment_tier": deployment_tier,
        "market": "KRW-BTC",
        "interval": "1m",
        "dataset": {
            "source": "sqlite_candles",
            "snapshot_id": "candles_v1",
            "train": {"start": "2023-01-01", "end": "2023-01-01"},
            "validation": {"start": "2023-01-02", "end": "2023-01-02"},
            "final_holdout": {"start": "2023-01-03", "end": "2023-01-03"},
        },
        "parameter_space": {
            "SMA_SHORT": [7],
            "SMA_LONG": [30],
            "SMA_FILTER_GAP_MIN_RATIO": [0.0012],
        },
        "cost_model": {"fee_rate": 0.0004, "slippage_bps": [10]},
        "acceptance_gate": {
            "min_trade_count": 30,
            "max_mdd_pct": 15,
            "min_profit_factor": 1.2,
            "oos_return_must_be_positive": True,
            "parameter_stability_required": True,
            "walk_forward_required": True,
            "final_holdout_required_for_promotion": True,
            "metrics_contract_required": True,
            "min_cagr_pct": 0,
            "min_expectancy_per_trade_krw": 0,
            "reject_open_position_at_end": True,
        },
        "walk_forward": {
            "train_window_days": 2,
            "test_window_days": 1,
            "step_days": 1,
            "min_windows": 1,
        },
    }


def _base_scenario() -> dict[str, object]:
    return {
        "scenario_role": "base",
        "label": "realistic_bithumb_app_fee_0004",
        "fee_rate": 0.0004,
        "fee_source": "operator_declared_bithumb_app_fee",
        "fee_authority_policy": "runtime_fee_authority_must_match_or_fail",
        "slippage_bps": 10,
        "slippage_source": "execution_calibration",
        "promotable_as_base": True,
    }


def _stress_scenario() -> dict[str, object]:
    return {
        "scenario_role": "stress",
        "label": "stress_fee_0025_slippage_20bps",
        "fee_rate": 0.0025,
        "fee_source": "stress_assumption",
        "fee_authority_policy": "not_promotable_as_runtime_base",
        "slippage_bps": 20,
        "slippage_source": "stress_assumption",
        "promotable_as_base": False,
    }


def test_production_bound_manifest_without_base_cost_assumption_fails() -> None:
    payload = _manifest()
    payload["execution_model"] = {
        "scenario_policy": "must_pass_base_and_survive_stress",
        "scenarios": [_stress_scenario()],
        "calibration_required": True,
    }

    with pytest.raises(ManifestValidationError, match="production_base_cost_assumption_required"):
        parse_manifest(payload)


def test_production_bound_legacy_cost_model_fails() -> None:
    with pytest.raises(ManifestValidationError, match="production_legacy_cost_model_not_promotable"):
        parse_manifest(_manifest())


def test_production_bound_unlabeled_base_cost_assumption_fails() -> None:
    payload = _manifest()
    base = _base_scenario()
    base["label"] = ""
    payload["execution_model"] = {
        "scenario_policy": "must_pass_base_and_survive_stress",
        "scenarios": [base, _stress_scenario()],
        "calibration_required": True,
    }

    with pytest.raises(ManifestValidationError, match="production_cost_assumption_label_required"):
        parse_manifest(payload)


def test_explicit_base_and_stress_cost_assumptions_pass_policy() -> None:
    payload = _manifest()
    payload["execution_model"] = {
        "scenario_policy": "must_pass_base_and_survive_stress",
        "scenarios": [_base_scenario(), _stress_scenario()],
        "calibration_required": True,
    }

    manifest = parse_manifest(payload)

    assert manifest.execution_model.scenarios[0].cost_assumption is not None
    assert manifest.execution_model.scenarios[0].cost_assumption.promotable_as_base is True
    assert manifest.execution_model.scenarios[1].scenario_role == "stress"


def test_legacy_cost_model_research_only_is_marked_legacy_non_promotable() -> None:
    manifest = parse_manifest(_manifest(deployment_tier="research_only"))

    scenario = manifest.execution_model.scenarios[0]
    assert manifest.execution_model.source == "legacy_cost_model"
    assert scenario.cost_assumption is not None
    assert scenario.cost_assumption.fee_source == "legacy_cost_model"
    assert scenario.cost_assumption.promotable_as_base is False
