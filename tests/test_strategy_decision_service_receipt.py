from __future__ import annotations

from dataclasses import dataclass

import pytest

from bithumb_bot.strategy_decision_input import StrategyDecisionInputBundle
from bithumb_bot.strategy_decision_service import StrategyDecisionService, StrategyEvaluationRequest
from bithumb_bot.strategy_evaluation_receipt import validate_strategy_evaluation_receipt
from bithumb_bot.strategy_policy_contract import ExecutionConstraintSnapshot, PositionSnapshot, StrategyDecisionV2


@dataclass(frozen=True)
class _Market:
    pair: str = "KRW-BTC"

    def policy_input_payload(self) -> dict[str, object]:
        return {"pair": self.pair}


@dataclass(frozen=True)
class _Config:
    def policy_input_payload(self) -> dict[str, object]:
        return {"unit": True}


class _Policy:
    name = "unit_receipt_policy"

    def decide_snapshot(self, *, market, position, config, execution_context, exit_policy_config=None, rule_sources=None):
        del market, config, execution_context, exit_policy_config, rule_sources
        return StrategyDecisionV2(
            strategy_name=self.name,
            raw_signal="HOLD",
            raw_reason="unit",
            entry_signal="HOLD",
            entry_reason="unit",
            exit_signal="HOLD",
            exit_reason="unit",
            final_signal="HOLD",
            final_reason="unit",
            blocked_filters=(),
            entry_blocked=False,
            entry_block_reason=None,
            exit_rule=None,
            exit_evaluations=(),
            protective_exit_overrode_entry=False,
            exit_filter_suppression_prevented=False,
            position_snapshot=position,
            execution_intent=None,
            entry_decision=None,
            trace={},
            policy_hash="sha256:policy",
            policy_contract_hash="sha256:contract",
            policy_input_hash="sha256:input",
            policy_decision_hash="sha256:decision",
        )


def _request() -> StrategyEvaluationRequest:
    position = PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False)
    execution = ExecutionConstraintSnapshot(fee_rate_for_decision=0.0)
    bundle = StrategyDecisionInputBundle.build(
        strategy_name="unit_receipt_policy",
        market=_Market(),
        position=position,
        config=_Config(),
        execution_constraints=execution,
        exit_policy_config=None,
        materialized_parameters_hash="sha256:parameters",
        snapshot_projector_version="unit_receipt_projector.v1",
        snapshot_projector_hash="sha256:projector",
    )
    return StrategyEvaluationRequest(
        strategy_name="unit_receipt_policy",
        strategy_instance_id="unit:receipt",
        mode="research_promotion",
        strategy_policy=_Policy(),
        market_snapshot=bundle.market,
        position_snapshot=bundle.position,
        strategy_config=bundle.config,
        execution_constraints=bundle.execution_constraints,
        exit_policy_config=bundle.exit_policy_config,
        rule_sources={},
        approved_profile_hash=None,
        runtime_contract_hash=None,
        plugin_contract_hash=None,
        request_hash=None,
        provenance={
            "strategy_parameters_hash": "sha256:parameters",
            "approved_profile_hash_unavailable_reason": "unit",
            "plugin_contract_hash_unavailable_reason": "unit",
            "runtime_contract_hash_unavailable_reason": "unit",
            "runtime_decision_request_hash_unavailable_reason": "unit",
        },
        decision_input_bundle=bundle,
    )


def test_service_receipt_binds_input_and_decision_hash() -> None:
    result = StrategyDecisionService().evaluate(_request())

    receipt = dict(result.receipt)
    assert receipt == result.decision.trace["strategy_evaluation_receipt"]
    assert receipt["decision_input_bundle_hash"] == result.decision.trace["decision_input_bundle_hash"]
    assert receipt["policy_decision_hash"] == result.decision.policy_decision_hash
    validate_strategy_evaluation_receipt(
        receipt=receipt,
        decision=result.decision,
        expected_input_bundle_hash=str(result.decision.trace["decision_input_bundle_hash"]),
        expected_strategy_name="unit_receipt_policy",
        expected_mode="research_promotion",
    )


def test_forged_decision_boundary_without_receipt_rejected() -> None:
    decision = _Policy().decide_snapshot(
        market=_Market(),
        position=PositionSnapshot(in_position=False, entry_allowed=True, exit_allowed=False),
        config=_Config(),
        execution_context=ExecutionConstraintSnapshot(fee_rate_for_decision=0.0),
    )
    decision.trace["strategy_evaluation_provenance"] = {
        "decision_boundary": "StrategyDecisionService.evaluate"
    }

    with pytest.raises(ValueError, match="strategy_evaluation_receipt_missing"):
        validate_strategy_evaluation_receipt(receipt=decision.trace.get("strategy_evaluation_receipt"), decision=decision)
