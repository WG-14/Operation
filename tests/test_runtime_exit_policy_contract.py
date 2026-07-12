from __future__ import annotations

from operation.operation_strategy.registry import operation_exit_policy_materialization_from_parameters


def test_sma_operation_exit_policy_payload_and_hash_are_stable() -> None:
    materialized = operation_exit_policy_materialization_from_parameters(
        "sma_with_filter",
        {
            "SMA_SHORT": 7,
            "SMA_LONG": 30,
            "STRATEGY_EXIT_RULES": "stop_loss,opposite_cross,max_holding_time",
            "STRATEGY_EXIT_STOP_LOSS_RATIO": 0.01,
            "STRATEGY_EXIT_MAX_HOLDING_MIN": 10,
            "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO": 0.02,
            "STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO": 0.003,
        },
    )

    assert materialized.exit_policy["strategy_rules"] == ["opposite_cross"]
    assert materialized.exit_policy["opposite_cross"]["enabled"] is True
    assert materialized.exit_policy_hash == "sha256:df2ddfa265313b3b7ab6b1009df8575a45129240248388247daa1bf11fec41fb"
    assert materialized.exit_policy_contract_hash.startswith("sha256:")
