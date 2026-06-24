from __future__ import annotations

from bithumb_bot.paired_experiment_diff import (
    PAIRED_EXPERIMENT_STAGE_ORDER,
    compare_paired_experiment_stages,
)


def _lane(overrides: dict[str, dict[str, object]] | None = None) -> dict[str, object]:
    stages = {stage: {"hash": f"sha256:{stage}", "status": "ok"} for stage in PAIRED_EXPERIMENT_STAGE_ORDER}
    for stage, payload in dict(overrides or {}).items():
        stages[stage].update(payload)
    return {"stages": stages}


def test_first_divergence_is_daily_count_before_strategy_decision() -> None:
    result = compare_paired_experiment_stages(
        _lane({"daily_count_snapshot": {"hash": "sha256:shadow-count"}}),
        _lane(
            {
                "daily_count_snapshot": {"hash": "sha256:operational-count"},
                "strategy_decision": {"hash": "sha256:operational-strategy"},
            }
        ),
    )

    assert result["first_divergence"]["stage"] == "daily_count_snapshot"
    assert result["stage_diffs"][4]["status"] == "not_evaluated_after_divergence"


def test_first_divergence_is_submit_authority_after_matching_policy() -> None:
    result = compare_paired_experiment_stages(
        _lane({"submit_authority": {"hash": "sha256:shadow-submit"}}),
        _lane({"submit_authority": {"hash": "sha256:operational-submit"}}),
    )

    assert result["first_divergence"]["stage"] == "submit_authority"
    assert result["first_divergence"]["reason_code"] == "submit_authority_hash_mismatch"


def test_no_divergence_when_all_stage_hashes_match() -> None:
    result = compare_paired_experiment_stages(_lane(), _lane())

    assert result["ok"] is True
    assert result["first_divergence"]["stage"] == ""


def test_fail_closed_unmodeled_state_is_divergence_not_pass() -> None:
    result = compare_paired_experiment_stages(
        _lane({"position_snapshot": {"status": "fail_closed", "reason_code": "unmodeled_state"}}),
        _lane(),
    )

    assert result["ok"] is False
    assert result["first_divergence"]["stage"] == "position_snapshot"
    assert result["first_divergence"]["reason_code"] == "unmodeled_state"
