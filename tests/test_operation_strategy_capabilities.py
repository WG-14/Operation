from __future__ import annotations

import ast
from pathlib import Path

import pytest

import bithumb_bot.operation_strategy.capabilities as operation_capabilities
from bithumb_bot.operation_strategy.capabilities import (
    DataCapabilityRequirement,
    OperationStrategyDataRequirements,
    ResearchStrategyDataRequirements,
    RuntimeParameterAdapter,
    StrategyRuntimeCapabilities,
    normalized_data_capabilities,
)


def test_data_capability_requirement_normalizes_and_preserves_full_golden_payload() -> None:
    requirement = DataCapabilityRequirement(
        name=" Candles ",
        required=True,
        min_coverage_pct="99.5",
        evidence_level="closed_candle_lookback",
        source="runtime_provider",
        notes="fixed golden contract",
        lookback_rows="12",
        closed_candle_required=True,
        max_age_ms="60000",
        min_rows="10",
        lookback_window_ms="660000",
        min_density_pct="83.25",
        freshness_policy="fail_closed",
    )

    assert requirement.name == "candles"
    assert requirement.as_dict() == {
        "name": "candles",
        "required": True,
        "min_coverage_pct": 99.5,
        "evidence_level": "closed_candle_lookback",
        "source": "runtime_provider",
        "notes": "fixed golden contract",
        "lookback_rows": 12,
        "closed_candle_required": True,
        "max_age_ms": 60_000,
        "min_rows": 10,
        "lookback_window_ms": 660_000,
        "min_density_pct": 83.25,
        "freshness_policy": "fail_closed",
    }


@pytest.mark.parametrize("name", ("", " ", None))
def test_data_capability_requirement_rejects_empty_name(name: object) -> None:
    with pytest.raises(ValueError, match="data capability name must be non-empty"):
        DataCapabilityRequirement(name=name)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", (-0.01, 100.01))
def test_data_capability_requirement_validates_min_coverage_pct(value: float) -> None:
    with pytest.raises(ValueError, match="data capability min_coverage_pct must be between 0 and 100"):
        DataCapabilityRequirement(name="candles", min_coverage_pct=value)


@pytest.mark.parametrize("value", (-0.01, 100.01))
def test_data_capability_requirement_validates_min_density_pct(value: float) -> None:
    with pytest.raises(ValueError, match="data capability min_density_pct must be between 0 and 100"):
        DataCapabilityRequirement(name="candles", min_density_pct=value)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("lookback_rows", "data capability lookback_rows must be positive"),
        ("max_age_ms", "data capability max_age_ms must be positive"),
        ("min_rows", "data capability min_rows must be positive"),
        ("lookback_window_ms", "data capability lookback_window_ms must be positive"),
    ),
)
def test_data_capability_requirement_validates_positive_lookback_and_freshness_values(
    field: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DataCapabilityRequirement(name="candles", **{field: 0})


def test_normalized_data_capabilities_deduplicates_required_and_optional_with_required_priority() -> None:
    capabilities = normalized_data_capabilities(
        required_data=("top_of_book", "candles", "top_of_book"),
        optional_data=("candles", "trade_ticks", "top_of_book"),
    )

    assert [capability.as_dict() for capability in capabilities] == [
        {"name": "candles", "required": True},
        {"name": "orderbook_top", "required": True},
        {"name": "trades", "required": False},
    ]


def test_normalized_data_capabilities_are_sorted_after_alias_normalization() -> None:
    capabilities = normalized_data_capabilities(
        required_data=("trade_ticks",),
        optional_data=("top_of_book", "candles"),
        capabilities=(DataCapabilityRequirement("open_interest", required=False),),
    )

    assert [capability.name for capability in capabilities] == [
        "candles",
        "open_interest",
        "orderbook_top",
        "trades",
    ]


def test_research_strategy_data_requirements_capability_contract_payload_matches_golden_value() -> None:
    requirements = ResearchStrategyDataRequirements(
        required_data=("candles", "top_of_book"),
        optional_data=("trade_ticks", "candles"),
    )

    assert OperationStrategyDataRequirements is ResearchStrategyDataRequirements
    assert requirements.capability_contract_payload() == {
        "schema_version": 1,
        "required_data": ["candles", "top_of_book"],
        "optional_data": ["trade_ticks", "candles"],
        "capabilities": [
            {"name": "candles", "required": True},
            {"name": "orderbook_top", "required": True},
            {"name": "trades", "required": False},
        ],
    }


def test_runtime_parameter_adapter_preserves_fields() -> None:
    def from_env(env: dict[str, str]) -> dict[str, object]:
        return {"source": "env", "value": env["UNIT_KEY"]}

    def from_settings(settings: object) -> dict[str, object]:
        return {"source": "settings", "value": settings}

    adapter = RuntimeParameterAdapter(
        from_env=from_env,
        from_settings=from_settings,
        env_keys=("UNIT_KEY", "SECONDARY_KEY"),
    )

    assert adapter.from_env is from_env
    assert adapter.from_settings is from_settings
    assert adapter.env_keys == ("UNIT_KEY", "SECONDARY_KEY")


def test_strategy_runtime_capabilities_default_derivations_match_golden_value() -> None:
    capabilities = StrategyRuntimeCapabilities(
        promotion_runtime_decisions_supported=True,
        runtime_replay_supported=False,
    )

    assert capabilities.as_dict() == {
        "schema_version": 1,
        "research_supported": True,
        "replay_decisions_supported": False,
        "promotion_export_supported": True,
        "runtime_decision_supported": True,
        "promotion_runtime_decisions_supported": True,
        "runtime_replay_supported": False,
        "research_only": False,
        "baseline_only": False,
        "live_dry_run_allowed": False,
        "live_real_order_allowed": False,
        "approved_profile_required": True,
        "accepts_empty_runtime_parameters": False,
        "fail_closed_reason": "strategy_runtime_capability_missing",
    }


def test_strategy_runtime_capabilities_full_as_dict_payload_matches_golden_value() -> None:
    capabilities = StrategyRuntimeCapabilities(
        promotion_runtime_decisions_supported=True,
        runtime_replay_supported=True,
        research_only=False,
        baseline_only=False,
        live_dry_run_allowed=True,
        live_real_order_allowed=True,
        approved_profile_required=True,
        accepts_empty_runtime_parameters=True,
        fail_closed_reason=" Custom Capability Reason ",
        research_supported=False,
        replay_decisions_supported=False,
        promotion_export_supported=False,
        runtime_decision_supported=False,
    )

    assert capabilities.as_dict() == {
        "schema_version": 1,
        "research_supported": False,
        "replay_decisions_supported": False,
        "promotion_export_supported": False,
        "runtime_decision_supported": False,
        "promotion_runtime_decisions_supported": True,
        "runtime_replay_supported": True,
        "research_only": False,
        "baseline_only": False,
        "live_dry_run_allowed": True,
        "live_real_order_allowed": True,
        "approved_profile_required": True,
        "accepts_empty_runtime_parameters": True,
        "fail_closed_reason": "custom capability reason",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"fail_closed_reason": " "}, "strategy runtime capability fail_closed_reason must be non-empty"),
        (
            {"research_only": True, "live_dry_run_allowed": True},
            "research-only or baseline-only strategy cannot be live eligible",
        ),
        (
            {"baseline_only": True, "live_real_order_allowed": True},
            "research-only or baseline-only strategy cannot be live eligible",
        ),
        (
            {"live_real_order_allowed": True},
            "live real-order eligibility requires live dry-run eligibility",
        ),
        (
            {"live_dry_run_allowed": True},
            "live dry-run eligibility requires promotion runtime decision support",
        ),
        (
            {
                "promotion_runtime_decisions_supported": True,
                "live_dry_run_allowed": True,
                "live_real_order_allowed": True,
                "approved_profile_required": False,
            },
            "live real-order eligibility requires an approved profile",
        ),
    ),
)
def test_strategy_runtime_capabilities_preserve_fail_closed_validation(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        StrategyRuntimeCapabilities(**{
            "promotion_runtime_decisions_supported": False,
            "runtime_replay_supported": False,
            **kwargs,
        })


def test_operation_strategy_capabilities_does_not_import_research() -> None:
    source = ast.parse(Path(operation_capabilities.__file__).read_text(encoding="utf-8"))
    assert all(
        not (
            isinstance(node, ast.Import)
            and any(alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.") for alias in node.names)
        )
        and not (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "bithumb_bot.research" or node.module.startswith("bithumb_bot.research."))
        )
        for node in ast.walk(source)
    )


def test_research_registry_reexports_operation_capability_class_objects_during_transition() -> None:
    """Remove when the research directory is deleted and this compatibility path no longer exists."""
    from bithumb_bot.research import strategy_registry

    assert strategy_registry.DataCapabilityRequirement is DataCapabilityRequirement
    assert strategy_registry.ResearchStrategyDataRequirements is ResearchStrategyDataRequirements
    assert strategy_registry.RuntimeParameterAdapter is RuntimeParameterAdapter
    assert strategy_registry.StrategyRuntimeCapabilities is StrategyRuntimeCapabilities
