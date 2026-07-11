from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from bithumb_bot.runtime_data_capabilities import normalize_runtime_data_capability


RuntimeEnvParameterExtractor = Callable[[dict[str, str]], dict[str, Any]]
RuntimeSettingsParameterExtractor = Callable[[object], dict[str, Any]]


@dataclass(frozen=True)
class DataCapabilityRequirement:
    name: str
    required: bool = True
    min_coverage_pct: float | None = None
    evidence_level: str | None = None
    source: str | None = None
    notes: str | None = None
    lookback_rows: int | None = None
    closed_candle_required: bool = False
    max_age_ms: int | None = None
    min_rows: int | None = None
    lookback_window_ms: int | None = None
    min_density_pct: float | None = None
    freshness_policy: str | None = None

    def __post_init__(self) -> None:
        normalized = str(self.name or "").strip().lower()
        if not normalized:
            raise ValueError("data capability name must be non-empty")
        object.__setattr__(self, "name", normalized)
        if self.min_coverage_pct is not None:
            coverage = float(self.min_coverage_pct)
            if coverage < 0.0 or coverage > 100.0:
                raise ValueError("data capability min_coverage_pct must be between 0 and 100")
            object.__setattr__(self, "min_coverage_pct", coverage)
        if self.lookback_rows is not None:
            rows = int(self.lookback_rows)
            if rows < 1:
                raise ValueError("data capability lookback_rows must be positive")
            object.__setattr__(self, "lookback_rows", rows)
        for field in ("max_age_ms", "min_rows", "lookback_window_ms"):
            value = getattr(self, field)
            if value is None:
                continue
            normalized_int = int(value)
            if normalized_int < 1:
                raise ValueError(f"data capability {field} must be positive")
            object.__setattr__(self, field, normalized_int)
        if self.min_density_pct is not None:
            density = float(self.min_density_pct)
            if density < 0.0 or density > 100.0:
                raise ValueError("data capability min_density_pct must be between 0 and 100")
            object.__setattr__(self, "min_density_pct", density)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "required": bool(self.required),
        }
        if self.min_coverage_pct is not None:
            payload["min_coverage_pct"] = float(self.min_coverage_pct)
        if self.evidence_level is not None:
            payload["evidence_level"] = str(self.evidence_level)
        if self.source is not None:
            payload["source"] = str(self.source)
        if self.notes is not None:
            payload["notes"] = str(self.notes)
        if self.lookback_rows is not None:
            payload["lookback_rows"] = int(self.lookback_rows)
        if self.closed_candle_required:
            payload["closed_candle_required"] = True
        if self.max_age_ms is not None:
            payload["max_age_ms"] = int(self.max_age_ms)
        if self.min_rows is not None:
            payload["min_rows"] = int(self.min_rows)
        if self.lookback_window_ms is not None:
            payload["lookback_window_ms"] = int(self.lookback_window_ms)
        if self.min_density_pct is not None:
            payload["min_density_pct"] = float(self.min_density_pct)
        if self.freshness_policy is not None:
            payload["freshness_policy"] = str(self.freshness_policy)
        return payload


@dataclass(frozen=True)
class ResearchStrategyDataRequirements:
    required_data: tuple[str, ...]
    optional_data: tuple[str, ...] = ()
    unsupported_without: tuple[str, ...] = ()
    capabilities: tuple[DataCapabilityRequirement, ...] = ()

    def normalized_capabilities(self) -> tuple[DataCapabilityRequirement, ...]:
        return normalized_data_capabilities(
            required_data=self.required_data,
            optional_data=self.optional_data,
            capabilities=self.capabilities,
        )

    def capability_contract_payload(self) -> dict[str, Any]:
        capabilities = self.normalized_capabilities()
        return {
            "schema_version": 1,
            "required_data": list(self.required_data),
            "optional_data": list(self.optional_data),
            "capabilities": [capability.as_dict() for capability in capabilities],
        }


OperationStrategyDataRequirements = ResearchStrategyDataRequirements
RuntimeDataRequirementBuilder = Callable[[object | None], ResearchStrategyDataRequirements]


def normalized_data_capabilities(
    *,
    required_data: tuple[str, ...],
    optional_data: tuple[str, ...] = (),
    capabilities: tuple[DataCapabilityRequirement, ...] = (),
) -> tuple[DataCapabilityRequirement, ...]:
    by_name: dict[str, DataCapabilityRequirement] = {}
    for raw_name in required_data:
        name = normalize_runtime_data_capability(str(raw_name))
        if name:
            by_name[name] = DataCapabilityRequirement(name=name, required=True)
    for raw_name in optional_data:
        name = normalize_runtime_data_capability(str(raw_name))
        if name and name not in by_name:
            by_name[name] = DataCapabilityRequirement(name=name, required=False)
    for capability in capabilities:
        normalized_name = normalize_runtime_data_capability(capability.name)
        by_name[normalized_name] = DataCapabilityRequirement(
            name=normalized_name,
            required=capability.required,
            min_coverage_pct=capability.min_coverage_pct,
            evidence_level=capability.evidence_level,
            source=capability.source,
            notes=capability.notes,
            lookback_rows=capability.lookback_rows,
            closed_candle_required=capability.closed_candle_required,
            max_age_ms=capability.max_age_ms,
            min_rows=capability.min_rows,
            lookback_window_ms=capability.lookback_window_ms,
            min_density_pct=capability.min_density_pct,
            freshness_policy=capability.freshness_policy,
        )
    return tuple(by_name[name] for name in sorted(by_name))


@dataclass(frozen=True)
class RuntimeParameterAdapter:
    """Legacy runtime parameter compatibility adapter.

    `from_settings` is paper_legacy_compat_only. Promotion, live dry-run, and
    live real-order runtime authority must come from approved profiles or
    structured runtime strategy specs, not settings-derived strategy fields.
    """

    from_env: RuntimeEnvParameterExtractor
    from_settings: RuntimeSettingsParameterExtractor
    env_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyRuntimeCapabilities:
    promotion_runtime_decisions_supported: bool
    runtime_replay_supported: bool
    research_only: bool = False
    baseline_only: bool = False
    live_dry_run_allowed: bool = False
    live_real_order_allowed: bool = False
    approved_profile_required: bool = True
    accepts_empty_runtime_parameters: bool = False
    fail_closed_reason: str = "strategy_runtime_capability_missing"
    research_supported: bool | None = None
    replay_decisions_supported: bool | None = None
    promotion_export_supported: bool | None = None
    runtime_decision_supported: bool | None = None

    def __post_init__(self) -> None:
        reason = str(self.fail_closed_reason or "").strip().lower()
        if not reason:
            raise ValueError("strategy runtime capability fail_closed_reason must be non-empty")
        object.__setattr__(self, "fail_closed_reason", reason)
        if self.research_supported is None:
            object.__setattr__(self, "research_supported", not bool(self.baseline_only))
        if self.replay_decisions_supported is None:
            object.__setattr__(self, "replay_decisions_supported", bool(self.runtime_replay_supported))
        if self.promotion_export_supported is None:
            object.__setattr__(
                self,
                "promotion_export_supported",
                bool(self.promotion_runtime_decisions_supported),
            )
        if self.runtime_decision_supported is None:
            object.__setattr__(
                self,
                "runtime_decision_supported",
                bool(self.promotion_runtime_decisions_supported),
            )
        if bool(self.research_only) or bool(self.baseline_only):
            if self.promotion_runtime_decisions_supported:
                raise ValueError("research-only or baseline-only strategy cannot support promotion runtime decisions")
            if self.live_dry_run_allowed or self.live_real_order_allowed:
                raise ValueError("research-only or baseline-only strategy cannot be live eligible")
        if self.live_real_order_allowed and not self.live_dry_run_allowed:
            raise ValueError("live real-order eligibility requires live dry-run eligibility")
        if self.live_dry_run_allowed and not self.promotion_runtime_decisions_supported:
            raise ValueError("live dry-run eligibility requires promotion runtime decision support")
        if self.live_real_order_allowed and not self.approved_profile_required:
            raise ValueError("live real-order eligibility requires an approved profile")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "research_supported": bool(self.research_supported),
            "replay_decisions_supported": bool(self.replay_decisions_supported),
            "promotion_export_supported": bool(self.promotion_export_supported),
            "runtime_decision_supported": bool(self.runtime_decision_supported),
            "promotion_runtime_decisions_supported": bool(self.promotion_runtime_decisions_supported),
            "runtime_replay_supported": bool(self.runtime_replay_supported),
            "research_only": bool(self.research_only),
            "baseline_only": bool(self.baseline_only),
            "live_dry_run_allowed": bool(self.live_dry_run_allowed),
            "live_real_order_allowed": bool(self.live_real_order_allowed),
            "approved_profile_required": bool(self.approved_profile_required),
            "accepts_empty_runtime_parameters": bool(self.accepts_empty_runtime_parameters),
            "fail_closed_reason": self.fail_closed_reason,
        }
