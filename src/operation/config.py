from __future__ import annotations

import hashlib
import json
import math
import os
import logging
import re
import subprocess
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Literal

from .markets import (
    canonical_market_id,
    MarketCatalogError,
    MarketRegistry,
    evaluate_market_warning_policy,
    UnsupportedMarketError,
    get_market_registry,
    normalize_market_id,
    validate_exchange_market_id,
)
from .market_catalog_snapshot import record_market_catalog_snapshot
from .notifier import is_configured as notifier_is_configured
from .paths import PathManager, PathPolicyError, validate_runtime_root_separation
from .submit_authority_policy import submit_authority_policy_from_settings
from .messages import (
    ACCOUNTS_PREFLIGHT_AUTH_FAILED,
    ACCOUNTS_PREFLIGHT_TRANSPORT_FAILED,
    CONFIG_LINT_MESSAGES,
    LIVE_DB_PATH_REQUIRED,
)
from .config_spec import (
    CONFIG_SCHEMA_VERSION,
    ENV_SPECS,
    SPEC_BY_NAME,
    artifact_hash,
    config_spec_hash,
    settings_contract_failures,
)
from .operation_approval import (
    approved_profile_path_from_env,
    expected_profile_modes_for_runtime,
    runtime_contract_from_settings,
    verify_profile_against_runtime,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    PATH_MANAGER = PathManager.from_env(PROJECT_ROOT)
except PathPolicyError as exc:
    raise ValueError(str(exc)) from exc
LIVE_DB_PATH_REQUIRED_MSG = (
    f"{LIVE_DB_PATH_REQUIRED.message} "
    f"reason_code={LIVE_DB_PATH_REQUIRED.reason_code} "
    f"action={LIVE_DB_PATH_REQUIRED.recommended_action}"
)
LIVE_SUBMIT_CONTRACT_PROFILE_V1 = "live_explicit_submit_plan_v1"
LIVE_ORDER_RULE_FALLBACK_PROFILE_PERSISTED_SNAPSHOT_REQUIRED = "persisted_snapshot_required"
LIVE_ORDER_RULE_FALLBACK_PROFILE_ALLOW_LOCAL_FALLBACK = "allow_local_fallback"
PAPER_ONLY_ENV_KEYS = (
    "START_CASH_KRW",
    "BUY_FRACTION",
    "FEE_RATE",
    "PAPER_FEE_RATE",
    "PAPER_FEE_RATE_ESTIMATE",
    "SLIPPAGE_BPS",
    "PAPER_EXECUTION_MODEL",
    "PAPER_EXECUTION_STRESS_SEED",
    "PAPER_EXECUTION_LATENCY_MS",
    "PAPER_EXECUTION_PARTIAL_FILL_RATE",
    "PAPER_EXECUTION_PARTIAL_FILL_FRACTION",
    "PAPER_EXECUTION_ORDER_FAILURE_RATE",
)
ALLOWED_RUNTIME_MODES = ("paper", "live")
DEFAULT_CANONICAL_MARKET = "KRW-BTC"
LEGACY_V1_ORDER_SCAN_ENV_KEYS = (
    "OPERATION_V1_ORDER_SCAN_MARKET",
    "OPERATION_V1_ORDER_SCAN_STATES",
    "OPERATION_V1_ORDER_SCAN_LIMIT",
)
LOG = logging.getLogger(__name__)
_MARKET_TOKEN_RE = re.compile(r"^[A-Z0-9]+$")
_CANONICAL_MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


def parse_bool_env(key: str, default: str = "false") -> bool:
    v = os.getenv(key, default)
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def parse_bool_env_strict(key: str, default: str) -> bool:
    raw = os.getenv(key)
    candidate = raw if raw is not None and raw.strip() != "" else default
    normalized = str(candidate).strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    raise ValueError(
        f"{key} must be a boolean value (one of: true/false/1/0/yes/no/on/off), got {candidate!r}"
    )


def parse_float_env(key: str, default: str) -> float:
    raw = os.getenv(key)
    candidate = raw if raw is not None and raw.strip() != "" else default
    try:
        return float(candidate)
    except ValueError as exc:
        raise ValueError(f"{key} must be a float-compatible value, got {candidate!r}") from exc


def parse_non_negative_float_env(key: str, default: str) -> float:
    value = parse_float_env(key, default)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must be a finite value >= 0, got {value!r}")
    return value


def parse_deprecated_ignored_bool_env(key: str, *, fixed_value: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is not None and str(raw).strip() != "":
        LOG.warning("%s is deprecated and ignored; runtime behavior remains fixed at %s", key, int(bool(fixed_value)))
    return bool(fixed_value)


def resolve_db_path(path: str) -> str:
    p = Path(path)
    if str(p) == ":memory:":
        return str(p)
    if p.is_absolute():
        return str(p.resolve())
    raise ValueError(
        f"DB_PATH must be an absolute path (got relative path: {path!r}); "
        "use PathManager-managed absolute DATA_ROOT path"
    )


def _validate_live_db_path_policy(resolved_db_path: str) -> None:
    db_path = Path(resolved_db_path).resolve()
    if PathManager._contains_segment(db_path, "paper"):
        raise LiveModeValidationError("DB_PATH must not point to a paper-scoped path when MODE=live")
    if PathManager._is_within(db_path, PROJECT_ROOT.resolve()):
        raise LiveModeValidationError("DB_PATH must be outside repository when MODE=live")


def resolve_db_path_for_mode(path: str, *, mode: str) -> str:
    resolved = resolve_db_path(path)
    normalized_mode = str(mode or "").strip().lower() or "paper"
    if normalized_mode == "live":
        _validate_live_db_path_policy(resolved)
    return resolved


def resolve_db_path_for_connection(path: str, *, mode: str | None = None) -> str:
    normalized_mode = str(mode or os.getenv("MODE", "paper") or "paper").strip().lower() or "paper"
    return resolve_db_path_for_mode(path, mode=normalized_mode)


def prepare_db_path_for_connection(path: str, *, mode: str | None = None) -> str:
    normalized_mode = str(mode or os.getenv("MODE", "paper") or "paper").strip().lower() or "paper"
    resolved = resolve_db_path_for_mode(path, mode=normalized_mode)
    if resolved != ":memory:":
        PATH_MANAGER.ensure_parent_dir(Path(resolved))
    return resolved


LiveModeValidationError = globals().get(
    "LiveModeValidationError",
    type("LiveModeValidationError", (ValueError,), {}),
)


ModeValidationError = globals().get(
    "ModeValidationError",
    type("ModeValidationError", (ValueError,), {}),
)


MarketPreflightValidationError = globals().get(
    "MarketPreflightValidationError",
    type("MarketPreflightValidationError", (ValueError,), {}),
)


class AccountsPreflightValidationError(ValueError):
    pass


def validate_accounts_preflight(cfg: Settings) -> None:
    del cfg


def resolve_db_path_from_env(mode: str) -> str:
    raw_db_path = os.getenv("DB_PATH")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode == "live" and (raw_db_path is None or not raw_db_path.strip()):
        raise LiveModeValidationError(LIVE_DB_PATH_REQUIRED_MSG)
    if raw_db_path and raw_db_path.strip():
        return resolve_db_path_for_mode(raw_db_path, mode=normalized_mode)
    return resolve_db_path_for_mode(str(PATH_MANAGER.primary_db_path()), mode=normalized_mode)


def resolve_strategy_name_from_env() -> str:
    raw = os.getenv("STRATEGY_NAME")
    normalized = str(raw or "").strip().lower()
    if normalized:
        return normalized
    from .compat.sma_runtime_compat import strategy_name_from_legacy_compat_env

    # An offline runtime must remain executable with an empty explicit env.
    # This built-in strategy still passes the shared runtime-contract checks;
    # paper execution remains bounded by its own local-only safeguards.
    return strategy_name_from_legacy_compat_env() or "sma_with_filter"


@dataclass(frozen=True)
class SingleStrategyProfileBinding:
    strategy_name: str
    profile_path: str
    profile_hash: str | None


@dataclass(frozen=True)
class StrategySetProfileBinding:
    strategy_instance_id: str
    strategy_name: str
    profile_path: str
    profile_hash: str


@dataclass(frozen=True)
class RuntimeProfileBindingReport:
    selection_kind: Literal["single_strategy", "multi_strategy"]
    ok: bool
    bindings: tuple[SingleStrategyProfileBinding | StrategySetProfileBinding, ...]
    issues: tuple[str, ...]
    runtime_strategy_set_source: str
    global_profile_selector_present: bool


def _global_approved_profile_selector(cfg: object) -> str:
    return str(getattr(cfg, "OPERATION_APPROVAL_PATH", "") or "").strip()


def _resolve_runtime_strategy_set_for_live_startup(cfg: Settings):
    from .runtime_strategy_set import RuntimeStrategySetResolver, runtime_authority_scope_from_settings

    return RuntimeStrategySetResolver(
        settings_obj=cfg,
        authority_scope=runtime_authority_scope_from_settings(cfg),
    ).resolve()


def _runtime_selection_kind(strategy_set: object) -> Literal["single_strategy", "multi_strategy"]:
    return "multi_strategy" if bool(getattr(strategy_set, "multi_strategy_enabled", False)) else "single_strategy"


def _approved_strategy_profile_path_from_cfg(cfg: Settings) -> str:
    return str(getattr(cfg, "OPERATION_APPROVAL_PATH", "") or "").strip()


def validate_runtime_profile_bindings_for_live_startup(
    cfg: Settings,
    *,
    expected_profile_modes: set[str] | None = None,
) -> RuntimeProfileBindingReport:
    from .runtime_strategy_set import (
        ProfileAuthorityContext,
        RuntimeDecisionRequestBuilder,
        derive_strategy_instance_id,
        validate_runtime_strategy_set_profile_binding,
    )

    strategy_set = _resolve_runtime_strategy_set_for_live_startup(cfg)
    selection_kind = _runtime_selection_kind(strategy_set)
    global_profile = _global_approved_profile_selector(cfg)
    issues: list[str] = []
    bindings: list[SingleStrategyProfileBinding | StrategySetProfileBinding] = []

    if selection_kind == "single_strategy":
        runtime_contract = runtime_contract_from_settings(cfg)
        profile_path = str(runtime_contract.get("profile_selector") or "").strip()
        profile_required = bool(cfg.LIVE_DRY_RUN or cfg.LIVE_REAL_ORDER_ARMED)
        if expected_profile_modes is None:
            expected_modes, mode_reason = expected_profile_modes_for_runtime(runtime_contract)
        else:
            expected_modes, mode_reason = expected_profile_modes, None
        profile_result = verify_profile_against_runtime(
            profile_path=profile_path,
            runtime=runtime_contract,
            require_profile=profile_required,
            expected_profile_modes=expected_modes,
            expected_profile_mode_reason=mode_reason,
            verify_source_promotion=True,
        )
        bindings.append(
            SingleStrategyProfileBinding(
                strategy_name=str(runtime_contract.get("strategy_name") or cfg.STRATEGY_NAME),
                profile_path=profile_path,
                profile_hash=profile_result.profile_hash,
            )
        )
        if not profile_result.ok:
            issues.append(
                "approved profile verification failed: "
                f"reason={profile_result.reason} path={profile_result.profile_path or '-'}"
            )
        return RuntimeProfileBindingReport(
            selection_kind=selection_kind,
            ok=not issues,
            bindings=tuple(bindings),
            issues=tuple(issues),
            runtime_strategy_set_source=str(getattr(strategy_set, "source", "")),
            global_profile_selector_present=bool(global_profile),
        )

    issues.extend(validate_runtime_strategy_set_profile_binding(strategy_set, cfg))
    authority_context = ProfileAuthorityContext.for_strategy_set(
        strategy_set,
        settings_obj=cfg,
        expected_profile_modes=expected_profile_modes,
    )
    builder = RuntimeDecisionRequestBuilder(
        settings_obj=cfg,
    ).with_authority_context(authority_context)
    for spec in strategy_set.active_strategies:
        instance_id = derive_strategy_instance_id(spec)
        try:
            instance = builder.materialize_instance(spec)
        except Exception as exc:
            issues.append(
                f"{instance_id}:runtime_strategy_profile_binding_failed:{type(exc).__name__}:{exc}"
            )
            continue
        profile_path = str(instance.approved_profile_path or "").strip()
        profile_hash = str(instance.approved_profile_hash or "").strip()
        bindings.append(
            StrategySetProfileBinding(
                strategy_instance_id=instance.strategy_instance_id,
                strategy_name=instance.strategy_name,
                profile_path=profile_path,
                profile_hash=profile_hash,
            )
        )
        profile_result = verify_profile_against_runtime(
            profile_path=profile_path,
            runtime=dict(instance.runtime_contract),
            require_profile=True,
            expected_profile_modes=expected_profile_modes,
            verify_source_promotion=True,
        )
        if not profile_result.ok:
            issues.append(
                f"{instance.strategy_instance_id}:approved profile verification failed: "
                f"reason={profile_result.reason} path={profile_result.profile_path or '-'}"
            )
        elif profile_result.profile_hash != profile_hash:
            issues.append(
                f"{instance.strategy_instance_id}:approved_profile_hash_mismatch_for_runtime_strategy:"
                f"{instance.strategy_name}"
            )

    return RuntimeProfileBindingReport(
        selection_kind=selection_kind,
        ok=not issues,
        bindings=tuple(bindings),
        issues=tuple(issues),
        runtime_strategy_set_source=str(getattr(strategy_set, "source", "")),
        global_profile_selector_present=bool(global_profile),
    )


def validate_live_strategy_selection(cfg: Settings) -> None:
    if str(cfg.MODE or "").strip().lower() != "live":
        return
    strategy_name = str(cfg.STRATEGY_NAME or "").strip().lower()
    approved_profile_path = _approved_strategy_profile_path_from_cfg(cfg)
    from .operation_strategy.registry import operation_strategy_runtime_capability_issues as strategy_runtime_capability_issues

    issues = strategy_runtime_capability_issues(
        strategy_name,
        live_dry_run=bool(cfg.LIVE_DRY_RUN),
        live_real_order_armed=bool(cfg.LIVE_REAL_ORDER_ARMED),
        approved_profile_path=approved_profile_path,
        require_promotion_runtime=True,
        require_runtime_replay=True,
        require_runtime_decision_adapter=True,
    )
    if issues:
        raise LiveModeValidationError(
            "live_strategy_capability_validation_failed: "
            f"STRATEGY_NAME={strategy_name!r}; reasons=" + ",".join(issues)
        )


def validate_runtime_strategy_set_selection(cfg: Settings) -> None:
    """Validate the exact active runtime strategy set before the run loop starts."""
    from .operation_strategy.registry import (
        OperationStrategyRegistryError as ResearchStrategyRegistryError,
        resolve_operation_strategy_plugin as resolve_research_strategy_plugin,
        operation_strategy_runtime_capability_issues as strategy_runtime_capability_issues,
    )
    from .runtime_strategy_decision import get_runtime_decision_adapter
    from .runtime_strategy_set import (
        ProfileAuthorityContext,
        RuntimeStrategySetResolver,
        derive_strategy_instance_id,
        runtime_authority_scope_from_settings,
        validate_runtime_strategy_set_market_scope,
        validate_runtime_strategy_set_profile_binding,
    )
    from . import runtime_strategy_set as runtime_strategy_set_module

    try:
        strategy_set = RuntimeStrategySetResolver(
            settings_obj=cfg,
            authority_scope=runtime_authority_scope_from_settings(cfg),
        ).resolve()
    except Exception as exc:
        raise LiveModeValidationError(
            f"runtime_strategy_set_selection_failed: resolve_failed:{type(exc).__name__}:{exc}"
        ) from exc

    issues: list[str] = []
    live_like = str(cfg.MODE or "").strip().lower() == "live"
    if live_like and strategy_set.source == "ACTIVE_STRATEGIES" and strategy_set.multi_strategy_enabled:
        issues.append(
            "ACTIVE_STRATEGIES:live_multi_strategy_requires_runtime_strategy_set_json"
        )
    if (
        live_like
        and strategy_set.multi_strategy_enabled
        and str(getattr(cfg, "EXECUTION_ENGINE", "") or "").strip().lower() != "target_delta"
    ):
        issues.append("live_multi_strategy_requires_execution_engine_target_delta")
    issues.extend(validate_runtime_strategy_set_market_scope(strategy_set, cfg))
    issues.extend(validate_runtime_strategy_set_profile_binding(strategy_set, cfg))
    active_instance_ids: set[str] = set()
    authority_context = ProfileAuthorityContext.for_strategy_set(strategy_set, settings_obj=cfg)
    request_builder = runtime_strategy_set_module.RuntimeDecisionRequestBuilder(
        settings_obj=cfg,
    ).with_authority_context(authority_context)
    for spec in strategy_set.active_strategies:
        instance_id = derive_strategy_instance_id(spec)
        if instance_id in active_instance_ids:
            issues.append(f"{instance_id}:runtime_strategy_duplicate_instance")
        active_instance_ids.add(instance_id)
        if str(spec.pair) != str(cfg.PAIR):
            issues.append(
                f"{instance_id}:runtime_strategy_pair_mismatch:"
                f"multi_pair_runtime_unsupported:settings_pair={cfg.PAIR}:spec_pair={spec.pair}"
            )
        try:
            plugin = resolve_research_strategy_plugin(spec.strategy_name)
        except ResearchStrategyRegistryError as exc:
            issues.append(f"{spec.strategy_name}:strategy_plugin_not_registered:{exc}")
            continue

        approved_profile_path = (
            str(spec.approved_profile_path or "").strip()
            or (
                ""
                if strategy_set.multi_strategy_enabled
                else str(cfg.OPERATION_APPROVAL_PATH or "").strip()
            )
            or (
                ""
                if strategy_set.multi_strategy_enabled
                else ""
            )
        )
        issues.extend(
            f"{spec.strategy_name}:{issue}"
            for issue in strategy_runtime_capability_issues(
                spec.strategy_name,
                live_dry_run=bool(cfg.LIVE_DRY_RUN),
                live_real_order_armed=bool(cfg.LIVE_REAL_ORDER_ARMED),
                approved_profile_path=approved_profile_path,
                # Paper execution is local-only and may use the documented
                # paper-legacy parameter authority. Promotion evidence stays
                # mandatory for every live-like startup.
                require_promotion_runtime=live_like,
                require_runtime_replay=live_like,
                require_runtime_decision_adapter=True,
            )
        )

        unexpected = sorted(set(spec.parameters or {}) - set(plugin.spec.accepted_parameter_names))
        if unexpected:
            issues.append(
                f"{spec.strategy_name}:runtime_strategy_parameters_unsupported:{','.join(unexpected)}"
            )
        elif spec.parameters:
            missing = sorted(set(plugin.spec.required_parameter_names) - set(spec.parameters or {}))
            if missing:
                issues.append(
                    f"{spec.strategy_name}:runtime_strategy_parameters_missing_required:{','.join(missing)}"
                )
        elif bool(plugin.runtime_capabilities.accepts_empty_runtime_parameters):
            pass

        try:
            adapter = get_runtime_decision_adapter(spec.strategy_name)
        except Exception as exc:
            issues.append(
                f"{spec.strategy_name}:runtime_decision_adapter_invalid:{type(exc).__name__}:{exc}"
            )
        else:
            if adapter is None:
                issues.append(f"{spec.strategy_name}:runtime_decision_adapter_unavailable")

        if spec.runtime_contract_hash and not str(spec.runtime_contract_hash).startswith("sha256:"):
            issues.append(f"{spec.strategy_name}:runtime_contract_hash_invalid")
        try:
            request_builder.materialize_instance(spec)
        except Exception as exc:
            issues.append(
                f"{instance_id}:runtime_strategy_materialization_failed:{type(exc).__name__}:{exc}"
            )

    if issues:
        raise LiveModeValidationError(
            "runtime_strategy_set_selection_failed: "
            f"source={strategy_set.source}; reasons=" + "; ".join(issues)
        )


def _normalize_config_market_input(raw_market: str, *, env_key: str, strict_canonical: bool) -> str:
    token = str(raw_market or "").strip().upper()
    if not token:
        raise ValueError(f"{env_key} must not be empty")

    if " " in token:
        raise ValueError(
            f"invalid {env_key} format: {raw_market!r}; market code must not contain spaces"
        )

    if strict_canonical:
        if not _CANONICAL_MARKET_RE.fullmatch(token):
            raise ValueError(
                f"invalid {env_key} format for MODE=live: {raw_market!r}; "
                "must be canonical QUOTE-BASE token like 'KRW-BTC' "
                "(legacy 'BTC_KRW' and bare 'BTC' are not allowed in live mode)"
            )
        return token

    if "-" in token:
        left, right = token.split("-", 1)
        if not (_MARKET_TOKEN_RE.fullmatch(left or "") and _MARKET_TOKEN_RE.fullmatch(right or "")):
            raise ValueError(
                f"invalid {env_key} format: {raw_market!r}; expected canonical 'KRW-BTC' style token"
            )
        return normalize_market_id(token)

    if "_" in token:
        left, right = token.split("_", 1)
        if not (_MARKET_TOKEN_RE.fullmatch(left or "") and _MARKET_TOKEN_RE.fullmatch(right or "")):
            raise ValueError(
                f"invalid {env_key} format: {raw_market!r}; expected legacy 'BTC_KRW' style token"
            )
        # PAIR historically used BASE_QUOTE (for example BTC_KRW), whereas
        # MARKET is always canonical QUOTE-BASE.  Preserve the paper-only
        # compatibility input at this boundary rather than letting it become
        # a different market identity downstream.
        if env_key == "PAIR":
            return normalize_market_id(f"{right}-{left}")
        return normalize_market_id(token)

    if not _MARKET_TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"invalid {env_key} format: {raw_market!r}; expected one of KRW-BTC, BTC_KRW, BTC"
        )
    return normalize_market_id(token)


def resolve_market_from_env() -> str:
    normalized_mode = str(os.getenv("MODE", "paper") or "paper").strip().lower() or "paper"
    strict_canonical = normalized_mode == "live"
    raw_market = os.getenv("MARKET")
    raw_pair = os.getenv("PAIR")

    has_market = raw_market is not None and raw_market.strip() != ""
    has_pair = raw_pair is not None and raw_pair.strip() != ""

    if has_market:
        canonical_market = _normalize_config_market_input(
            raw_market,
            env_key="MARKET",
            strict_canonical=True,
        )
    elif has_pair:
        canonical_market = _normalize_config_market_input(
            raw_pair,
            env_key="PAIR",
            strict_canonical=strict_canonical,
        )
    else:
        canonical_market = DEFAULT_CANONICAL_MARKET

    if has_market and has_pair:
        canonical_pair = _normalize_config_market_input(
            raw_pair,
            env_key="PAIR",
            strict_canonical=strict_canonical,
        )
        if canonical_pair != canonical_market:
            raise ValueError(
                "MARKET and PAIR resolve to different canonical markets: "
                f"MARKET={raw_market!r}->{canonical_market}, PAIR={raw_pair!r}->{canonical_pair}"
            )

    return canonical_market


def default_run_lock_path(mode: str) -> str:
    normalized_mode = (mode or "paper").strip().lower() or "paper"
    return str(PATH_MANAGER.run_lock_path_for_mode(normalized_mode))


def resolve_run_lock_path(path: str, *, mode: str | None = None) -> str:
    normalized_mode = str(mode or os.getenv("MODE", "paper") or "paper").strip().lower() or "paper"
    resolved = PathManager._resolve_explicit_root(
        "RUN_LOCK_PATH",
        path,
        normalized_mode,
        PROJECT_ROOT,
    )
    return str(resolved)


def resolve_run_lock_path_from_env(mode: str) -> str:
    normalized_mode = str(mode or "paper").strip().lower() or "paper"
    raw = os.getenv("RUN_LOCK_PATH")
    if raw and raw.strip():
        return resolve_run_lock_path(raw, mode=normalized_mode)
    return default_run_lock_path(normalized_mode)


@dataclass(frozen=True)
class Settings:
    # runtime
    MODE: str = os.getenv("MODE", "paper")
    PAIR: str = resolve_market_from_env()
    INTERVAL: str = os.getenv("INTERVAL", "1m")
    EVERY: int = int(os.getenv("EVERY", "60"))  # seconds
    HEALTH_MAX_CANDLE_AGE_SEC: int = int(os.getenv("HEALTH_MAX_CANDLE_AGE_SEC", "180"))
    HEALTH_MAX_ERROR_COUNT: int = int(os.getenv("HEALTH_MAX_ERROR_COUNT", "3"))

    # strategy
    STRATEGY_NAME: str = resolve_strategy_name_from_env()
    ACTIVE_STRATEGIES: str = os.getenv("ACTIVE_STRATEGIES", "").strip()
    RUNTIME_STRATEGY_SET_JSON: str = os.getenv("RUNTIME_STRATEGY_SET_JSON", "").strip()
    STRATEGY_PARAMETERS_JSON: str = os.getenv("STRATEGY_PARAMETERS_JSON", "").strip()
    COOLDOWN_MIN: int = int(os.getenv("COOLDOWN_MIN", "1"))
    MIN_GAP: float = float(os.getenv("MIN_GAP", "0.0003"))
    ENTRY_EDGE_BUFFER_RATIO: float = parse_float_env("ENTRY_EDGE_BUFFER_RATIO", "0.0005")
    STRATEGY_MIN_EXPECTED_EDGE_RATIO: float = parse_float_env(
        "STRATEGY_MIN_EXPECTED_EDGE_RATIO", "0"
    )
    MIN_NET_EDGE_KRW: float = parse_non_negative_float_env("MIN_NET_EDGE_KRW", "0")
    MIN_MARGIN_AFTER_COST_RATIO: float = parse_non_negative_float_env(
        "MIN_MARGIN_AFTER_COST_RATIO", "0"
    )
    PRE_TRADE_ECONOMICS_BLOCKING_ENABLED: bool = parse_bool_env_strict(
        "PRE_TRADE_ECONOMICS_BLOCKING_ENABLED",
        "false",
    )
    STRATEGY_EXIT_RULES: str = os.getenv("STRATEGY_EXIT_RULES", "stop_loss,opposite_cross,max_holding_time")
    STRATEGY_EXIT_STOP_LOSS_RATIO: float = parse_non_negative_float_env("STRATEGY_EXIT_STOP_LOSS_RATIO", "0")
    STRATEGY_EXIT_MAX_HOLDING_MIN: int = int(os.getenv("STRATEGY_EXIT_MAX_HOLDING_MIN", "0"))
    STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO: float = parse_float_env(
        "STRATEGY_EXIT_MIN_TAKE_PROFIT_RATIO", "0"
    )
    STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO: float = float(
        os.getenv("STRATEGY_EXIT_SMALL_LOSS_TOLERANCE_RATIO", "0")
    )
    OPERATION_APPROVAL_PATH: str = approved_profile_path_from_env()
    LIVE_PERFORMANCE_GATE_ENABLED: bool = parse_bool_env("LIVE_PERFORMANCE_GATE_ENABLED", "true")
    LIVE_PERFORMANCE_GATE_MIN_SAMPLE: int = int(os.getenv("LIVE_PERFORMANCE_GATE_MIN_SAMPLE", "30"))
    LIVE_PERFORMANCE_GATE_RECENT_LIMIT: int = int(os.getenv("LIVE_PERFORMANCE_GATE_RECENT_LIMIT", "200"))
    LIVE_PERFORMANCE_GATE_MIN_EXPECTANCY_KRW: float = parse_float_env(
        "LIVE_PERFORMANCE_GATE_MIN_EXPECTANCY_KRW", "0"
    )
    LIVE_PERFORMANCE_GATE_MIN_NET_PNL_KRW: float = parse_float_env(
        "LIVE_PERFORMANCE_GATE_MIN_NET_PNL_KRW", "0"
    )
    LIVE_PERFORMANCE_GATE_MIN_PROFIT_FACTOR: float = parse_float_env(
        "LIVE_PERFORMANCE_GATE_MIN_PROFIT_FACTOR", "1.0"
    )
    LIVE_PERFORMANCE_GATE_MAX_FEE_DRAG_RATIO: str = os.getenv(
        "LIVE_PERFORMANCE_GATE_MAX_FEE_DRAG_RATIO", ""
    )
    LIVE_PERFORMANCE_GATE_SCOPE: str = os.getenv(
        "LIVE_PERFORMANCE_GATE_SCOPE", "closed_lifecycles_recent"
    ).strip() or "closed_lifecycles_recent"
    LIVE_EXECUTION_QUALITY_GATE_ENABLED: bool = parse_bool_env(
        "LIVE_EXECUTION_QUALITY_GATE_ENABLED",
        "false",
    )
    LIVE_EXECUTION_QUALITY_MIN_SAMPLE: int = int(os.getenv("LIVE_EXECUTION_QUALITY_MIN_SAMPLE", "30"))
    LIVE_EXECUTION_QUALITY_MAX_P90_SLIPPAGE_BPS: float = parse_non_negative_float_env(
        "LIVE_EXECUTION_QUALITY_MAX_P90_SLIPPAGE_BPS",
        "20",
    )
    LIVE_EXECUTION_QUALITY_MAX_P95_FULL_FILL_LATENCY_MS: float = parse_non_negative_float_env(
        "LIVE_EXECUTION_QUALITY_MAX_P95_FULL_FILL_LATENCY_MS",
        "3000",
    )
    LIVE_EXECUTION_QUALITY_MAX_PARTIAL_FILL_RATE: float = parse_non_negative_float_env(
        "LIVE_EXECUTION_QUALITY_MAX_PARTIAL_FILL_RATE",
        "0.05",
    )
    LIVE_EXECUTION_QUALITY_MAX_MODEL_BREACH_RATE: float = parse_non_negative_float_env(
        "LIVE_EXECUTION_QUALITY_MAX_MODEL_BREACH_RATE",
        "0.10",
    )
    LIVE_EXECUTION_QUALITY_GATE_MODE: str = os.getenv(
        "LIVE_EXECUTION_QUALITY_GATE_MODE",
        "telemetry",
    ).strip().lower() or "telemetry"
    # Kept as explicit safety controls even while the live broker boundary is
    # unavailable.  They are read by shared runtime validation and must never
    # be inferred from a paper setting.
    LIVE_DRY_RUN: bool = parse_bool_env("LIVE_DRY_RUN", "false")
    LIVE_REAL_ORDER_ARMED: bool = parse_bool_env("LIVE_REAL_ORDER_ARMED", "false")

    # storage
    ENV_ROOT: str = str(PATH_MANAGER.config.env_root)
    RUN_ROOT: str = str(PATH_MANAGER.config.run_root)
    DATA_ROOT: str = str(PATH_MANAGER.config.data_root)
    LOG_ROOT: str = str(PATH_MANAGER.config.log_root)
    BACKUP_ROOT: str = str(PATH_MANAGER.config.backup_root)
    ARCHIVE_ROOT: str = str(PATH_MANAGER.config.archive_root) if PATH_MANAGER.config.archive_root else ""
    DB_PATH: str = resolve_db_path_from_env(os.getenv("MODE", "paper"))
    RUN_LOCK_PATH: str = resolve_run_lock_path_from_env(os.getenv("MODE", "paper"))
    DB_BUSY_TIMEOUT_MS: int = int(os.getenv("DB_BUSY_TIMEOUT_MS", "5000"))
    DB_LOCK_RETRY_COUNT: int = int(os.getenv("DB_LOCK_RETRY_COUNT", "2"))
    DB_LOCK_RETRY_BACKOFF_MS: int = int(os.getenv("DB_LOCK_RETRY_BACKOFF_MS", "50"))
    DECISION_PERSISTENCE_FAILURE_HALT_THRESHOLD: int = int(
        os.getenv("DECISION_PERSISTENCE_FAILURE_HALT_THRESHOLD", "3")
    )

    # paper portfolio
    START_CASH_KRW: float = float(os.getenv("START_CASH_KRW", "1000000"))
    BUY_FRACTION: float = float(os.getenv("BUY_FRACTION", "0.99"))
    EXECUTION_ENGINE: str = os.getenv("EXECUTION_ENGINE", "lot_native").strip().lower() or "lot_native"
    TARGET_EXECUTION_SHADOW: bool = parse_bool_env("TARGET_EXECUTION_SHADOW", "false")
    TARGET_EXPOSURE_KRW: float | None = (
        None
        if os.getenv("TARGET_EXPOSURE_KRW") in (None, "")
        else parse_non_negative_float_env("TARGET_EXPOSURE_KRW", os.getenv("TARGET_EXPOSURE_KRW", "0"))
    )
    TARGET_HOLD_POLICY: str = (
        os.getenv("TARGET_HOLD_POLICY", "maintain_previous_target").strip().lower()
        or "maintain_previous_target"
    )
    REQUIRE_BROKER_LOCAL_CONVERGENCE: bool = parse_bool_env("REQUIRE_BROKER_LOCAL_CONVERGENCE", "true")
    BLOCK_ON_OPEN_ORDER: bool = parse_bool_env("BLOCK_ON_OPEN_ORDER", "true")
    BLOCK_ON_SUBMIT_UNKNOWN: bool = parse_bool_env("BLOCK_ON_SUBMIT_UNKNOWN", "true")
    RESIDUAL_INVENTORY_MODE: str = os.getenv("RESIDUAL_INVENTORY_MODE", "block").strip().lower() or "block"
    RESIDUAL_LIVE_SELL_MODE: str = os.getenv("RESIDUAL_LIVE_SELL_MODE", "telemetry").strip().lower() or "telemetry"
    RESIDUAL_BUY_SIZING_MODE: str = os.getenv("RESIDUAL_BUY_SIZING_MODE", "telemetry").strip().lower() or "telemetry"
    # Common fallback fee rate. Operators should set live and paper fee estimates explicitly.
    FEE_RATE: float = float(os.getenv("FEE_RATE", "0.0004"))
    # Live pretrade cost estimate fallback: LIVE_FEE_RATE_ESTIMATE > FEE_RATE > 0.0004.
    LIVE_FEE_RATE_ESTIMATE: float = parse_float_env(
        "LIVE_FEE_RATE_ESTIMATE", os.getenv("FEE_RATE", "0.0004")
    )
    # Paper fill/PnL cost estimate fallback:
    #   PAPER_FEE_RATE > PAPER_FEE_RATE_ESTIMATE > FEE_RATE > LIVE_FEE_RATE_ESTIMATE > 0.0004
    PAPER_FEE_RATE: float = float(
        os.getenv(
            "PAPER_FEE_RATE",
            os.getenv(
                "PAPER_FEE_RATE_ESTIMATE",
                os.getenv("FEE_RATE", os.getenv("LIVE_FEE_RATE_ESTIMATE", "0.0004")),
            ),
        )
    )
    # Compatibility alias: PAPER_FEE_RATE_ESTIMATE resolves to PAPER_FEE_RATE.
    PAPER_FEE_RATE_ESTIMATE: float = PAPER_FEE_RATE
    SLIPPAGE_BPS: float = float(os.getenv("SLIPPAGE_BPS", "0"))
    PAPER_EXECUTION_MODEL: str = os.getenv("PAPER_EXECUTION_MODEL", "immediate").strip().lower() or "immediate"
    PAPER_EXECUTION_STRESS_SEED: str = os.getenv("PAPER_EXECUTION_STRESS_SEED", "").strip()
    PAPER_EXECUTION_LATENCY_MS: int = int(os.getenv("PAPER_EXECUTION_LATENCY_MS", "0"))
    PAPER_EXECUTION_PARTIAL_FILL_RATE: float = float(os.getenv("PAPER_EXECUTION_PARTIAL_FILL_RATE", "0"))
    PAPER_EXECUTION_PARTIAL_FILL_FRACTION: float = float(os.getenv("PAPER_EXECUTION_PARTIAL_FILL_FRACTION", "0.5"))
    PAPER_EXECUTION_ORDER_FAILURE_RATE: float = float(os.getenv("PAPER_EXECUTION_ORDER_FAILURE_RATE", "0"))
    # Conservative declared default: execution is referenced to the next
    # candle open, never the same candle close. Explicitly empty overrides
    # remain invalid at the operation-approval boundary.
    EXECUTION_FILL_REFERENCE_POLICY: str = os.getenv(
        "EXECUTION_FILL_REFERENCE_POLICY", "next_candle_open"
    ).strip()
    EXECUTION_DECISION_GUARD_MS: int = int(os.getenv("EXECUTION_DECISION_GUARD_MS", "0"))
    EXECUTION_MAX_QUOTE_WAIT_MS: int = int(os.getenv("EXECUTION_MAX_QUOTE_WAIT_MS", "0"))
    EXECUTION_MISSING_QUOTE_POLICY: str = os.getenv("EXECUTION_MISSING_QUOTE_POLICY", "warn").strip() or "warn"
    EXECUTION_MIN_REALITY_LEVEL_FOR_PROMOTION: str = os.getenv(
        "EXECUTION_MIN_REALITY_LEVEL_FOR_PROMOTION", ""
    ).strip()
    EXECUTION_ALLOW_SAME_CANDLE_CLOSE_FILL: bool = parse_bool_env(
        "EXECUTION_ALLOW_SAME_CANDLE_CLOSE_FILL", "false"
    )
    EXECUTION_QUOTE_SOURCE: str = os.getenv("EXECUTION_QUOTE_SOURCE", "").strip()
    EXECUTION_QUOTE_AGE_LIMIT_MS: int | None = (
        None
        if os.getenv("EXECUTION_QUOTE_AGE_LIMIT_MS") in (None, "")
        else int(os.getenv("EXECUTION_QUOTE_AGE_LIMIT_MS", "0"))
    )
    EXECUTION_TOP_OF_BOOK_REQUIRED: bool = parse_bool_env("EXECUTION_TOP_OF_BOOK_REQUIRED", "false")
    EXECUTION_TOP_OF_BOOK_IS_FULL_DEPTH: bool = parse_bool_env("EXECUTION_TOP_OF_BOOK_IS_FULL_DEPTH", "false")
    EXECUTION_DEPTH_REQUIRED: bool = parse_bool_env("EXECUTION_DEPTH_REQUIRED", "false")
    EXECUTION_TRADE_TICK_REQUIRED: bool = parse_bool_env("EXECUTION_TRADE_TICK_REQUIRED", "false")
    EXECUTION_QUEUE_POSITION_REQUIRED: bool = parse_bool_env("EXECUTION_QUEUE_POSITION_REQUIRED", "false")
    EXECUTION_MARKET_IMPACT_REQUIRED: bool = parse_bool_env("EXECUTION_MARKET_IMPACT_REQUIRED", "false")
    EXECUTION_INTRA_CANDLE_PATH_AVAILABLE: bool = parse_bool_env("EXECUTION_INTRA_CANDLE_PATH_AVAILABLE", "false")
    EXECUTION_REALITY_LEVEL: str = os.getenv("EXECUTION_REALITY_LEVEL", "").strip()
    EXECUTION_LATENCY_MODEL_TYPE: str = os.getenv("EXECUTION_LATENCY_MODEL_TYPE", "fixed_bps").strip() or "fixed_bps"
    EXECUTION_LATENCY_MS: int = int(os.getenv("EXECUTION_LATENCY_MS", "0"))
    EXECUTION_PARTIAL_FILL_MODEL_TYPE: str = (
        os.getenv("EXECUTION_PARTIAL_FILL_MODEL_TYPE", os.getenv("EXECUTION_LATENCY_MODEL_TYPE", "fixed_bps")).strip()
        or "fixed_bps"
    )
    EXECUTION_PARTIAL_FILL_RATE: float = float(os.getenv("EXECUTION_PARTIAL_FILL_RATE", "0"))
    EXECUTION_ORDER_FAILURE_MODEL_TYPE: str = (
        os.getenv("EXECUTION_ORDER_FAILURE_MODEL_TYPE", os.getenv("EXECUTION_LATENCY_MODEL_TYPE", "fixed_bps")).strip()
        or "fixed_bps"
    )
    EXECUTION_ORDER_FAILURE_RATE: float = float(os.getenv("EXECUTION_ORDER_FAILURE_RATE", "0"))
    EXECUTION_FEE_SOURCE: str = os.getenv("EXECUTION_FEE_SOURCE", "").strip()
    EXECUTION_SLIPPAGE_SOURCE: str = os.getenv("EXECUTION_SLIPPAGE_SOURCE", "").strip()
    EXECUTION_CALIBRATION_REQUIRED: bool = parse_bool_env("EXECUTION_CALIBRATION_REQUIRED", "false")
    EXECUTION_CALIBRATION_ARTIFACT_HASH: str = os.getenv("EXECUTION_CALIBRATION_ARTIFACT_HASH", "").strip()
    # Strategy entry slippage estimate in basis points for entry cost filtering.
    # Resolution order:
    #   STRATEGY_ENTRY_SLIPPAGE_BPS > MAX_MARKET_SLIPPAGE_BPS > SLIPPAGE_BPS > 0
    STRATEGY_ENTRY_SLIPPAGE_BPS: float = float(
        os.getenv(
            "STRATEGY_ENTRY_SLIPPAGE_BPS",
            os.getenv("MAX_MARKET_SLIPPAGE_BPS", os.getenv("SLIPPAGE_BPS", "0")),
        )
    )
    MAX_ORDERBOOK_SPREAD_BPS: float = float(os.getenv("MAX_ORDERBOOK_SPREAD_BPS", "100"))
    MAX_MARKET_SLIPPAGE_BPS: float = float(os.getenv("MAX_MARKET_SLIPPAGE_BPS", "0"))
    LIVE_PRICE_PROTECTION_MAX_SLIPPAGE_BPS: float = float(
        os.getenv("LIVE_PRICE_PROTECTION_MAX_SLIPPAGE_BPS", "0")
    )
    LIVE_PRICE_REFERENCE_MAX_AGE_SEC: int = int(os.getenv("LIVE_PRICE_REFERENCE_MAX_AGE_SEC", "0"))
    MIN_ORDER_NOTIONAL_KRW: float = float(os.getenv("MIN_ORDER_NOTIONAL_KRW", "0"))
    PRETRADE_BALANCE_BUFFER_BPS: float = float(os.getenv("PRETRADE_BALANCE_BUFFER_BPS", "0"))
    LIVE_MIN_ORDER_QTY: float = float(os.getenv("LIVE_MIN_ORDER_QTY", "0"))
    LIVE_ORDER_QTY_STEP: float = float(os.getenv("LIVE_ORDER_QTY_STEP", "0"))
    LIVE_ORDER_MAX_QTY_DECIMALS: int = int(os.getenv("LIVE_ORDER_MAX_QTY_DECIMALS", "0"))
    LIVE_FILL_FEE_ALERT_MIN_NOTIONAL_KRW: float = float(
        os.getenv("LIVE_FILL_FEE_ALERT_MIN_NOTIONAL_KRW", "10000")
    )
    LIVE_FILL_FEE_STRICT_MODE: bool = parse_bool_env("LIVE_FILL_FEE_STRICT_MODE", "false")
    LIVE_FILL_FEE_STRICT_MIN_NOTIONAL_KRW: float = float(
        os.getenv("LIVE_FILL_FEE_STRICT_MIN_NOTIONAL_KRW", "100000")
    )
    LIVE_FILL_FEE_RATIO_MIN: float = float(os.getenv("LIVE_FILL_FEE_RATIO_MIN", "0.000001"))
    LIVE_FILL_FEE_RATIO_MAX: float = float(os.getenv("LIVE_FILL_FEE_RATIO_MAX", "0.02"))
    LIVE_ALLOW_ORDER_RULE_FALLBACK: bool = parse_deprecated_ignored_bool_env(
        "LIVE_ALLOW_ORDER_RULE_FALLBACK",
        fixed_value=False,
    )
    LIVE_ORDER_RULE_FALLBACK_PROFILE: str = os.getenv(
        "LIVE_ORDER_RULE_FALLBACK_PROFILE",
        LIVE_ORDER_RULE_FALLBACK_PROFILE_PERSISTED_SNAPSHOT_REQUIRED,
    )
    LIVE_SUBMIT_CONTRACT_PROFILE: str = os.getenv(
        "LIVE_SUBMIT_CONTRACT_PROFILE",
        LIVE_SUBMIT_CONTRACT_PROFILE_V1,
    )
    BUY_PRICE_NONE_MARKET_TO_PRICE_ALIAS_ENABLED: bool = parse_deprecated_ignored_bool_env(
        "BUY_PRICE_NONE_MARKET_TO_PRICE_ALIAS_ENABLED",
        fixed_value=False,
    )

    # risk
    MAX_ORDER_KRW: float = float(os.getenv("MAX_ORDER_KRW", "0"))
    MAX_DAILY_LOSS_KRW: float = float(os.getenv("MAX_DAILY_LOSS_KRW", "0"))
    MAX_POSITION_LOSS_PCT: float = float(os.getenv("MAX_POSITION_LOSS_PCT", "0"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
    KILL_SWITCH: bool = parse_bool_env("KILL_SWITCH", "false")
    KILL_SWITCH_LIQUIDATE: bool = parse_bool_env("KILL_SWITCH_LIQUIDATE", "false")
    MAX_DAILY_ORDER_COUNT: int = int(os.getenv("MAX_DAILY_ORDER_COUNT", "0"))

    # Live execution has no configured broker adapter.
    MAX_OPEN_ORDER_AGE_SEC: int = int(os.getenv("MAX_OPEN_ORDER_AGE_SEC", "900"))
    OPEN_ORDER_RECONCILE_MIN_INTERVAL_SEC: int = int(
        os.getenv("OPEN_ORDER_RECONCILE_MIN_INTERVAL_SEC", "30")
    )
    MARKET_PREFLIGHT_BLOCK_ON_CATALOG_ERROR: bool = parse_bool_env(
        "MARKET_PREFLIGHT_BLOCK_ON_CATALOG_ERROR", ""
    )
    MARKET_PREFLIGHT_BLOCK_ON_WARNING: bool = parse_bool_env("MARKET_PREFLIGHT_BLOCK_ON_WARNING", "")
    MARKET_PREFLIGHT_WARNING_STATES: str = os.getenv("MARKET_PREFLIGHT_WARNING_STATES", "CAUTION")
    MARKET_REGISTRY_CACHE_TTL_SEC: float = parse_float_env("MARKET_REGISTRY_CACHE_TTL_SEC", "900")
    MARKET_PREFLIGHT_FORCE_REGISTRY_REFRESH: bool = parse_bool_env(
        "MARKET_PREFLIGHT_FORCE_REGISTRY_REFRESH", ""
    )
    MARKET_RUNTIME_REGISTRY_REFRESH_INTERVAL_SEC: float = parse_float_env(
        "MARKET_RUNTIME_REGISTRY_REFRESH_INTERVAL_SEC", "900"
    )

settings = Settings()


def _validate_settings_config_spec_contract() -> None:
    failures = settings_contract_failures({field.name for field in fields(Settings)})
    if failures:
        raise RuntimeError("; ".join(failures))


_validate_settings_config_spec_contract()


def validate_mode_or_raise(mode: str) -> None:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode in ALLOWED_RUNTIME_MODES:
        return
    allowed = ", ".join(ALLOWED_RUNTIME_MODES)
    raise ModeValidationError(
        f"invalid MODE={mode!r}; allowed values: {allowed}"
    )


def _fetch_market_registry_for_preflight(
    *,
    refresh: bool,
    ttl_seconds: float,
    is_details: bool,
) -> MarketRegistry:
    return get_market_registry(
        refresh=refresh,
        client=None,
        is_details=is_details,
        ttl_seconds=ttl_seconds,
    )


def _warning_state_set(raw_states: str) -> set[str]:
    states = {token.strip().upper() for token in str(raw_states or "").split(",")}
    cleaned = {token for token in states if token}
    if not cleaned:
        return {"CAUTION", "UNKNOWN"}
    cleaned.add("UNKNOWN")
    return cleaned


def _validate_market_registry_contract(
    cfg: Settings,
    *,
    context: str,
    record_snapshot: bool,
    force_refresh: bool,
) -> None:
    del context, record_snapshot, force_refresh
    try:
        canonical_market_id(str(cfg.PAIR or ""))
    except ValueError as exc:
        raise MarketPreflightValidationError(f"invalid configured market: {cfg.PAIR!r}") from exc


def validate_market_preflight(cfg: Settings) -> None:
    normalized_mode = str(cfg.MODE or "").strip().lower()
    force_refresh = (
        bool(cfg.MARKET_PREFLIGHT_FORCE_REGISTRY_REFRESH)
        if os.getenv("MARKET_PREFLIGHT_FORCE_REGISTRY_REFRESH") not in (None, "")
        else normalized_mode == "live"
    )
    _validate_market_registry_contract(
        cfg,
        context="preflight",
        record_snapshot=True,
        force_refresh=force_refresh,
    )

    try:
        validate_accounts_preflight(cfg)
    except AccountsPreflightValidationError as exc:
        if normalized_mode == "live":
            raise MarketPreflightValidationError(str(exc)) from exc
        LOG.warning(
            "accounts REST snapshot preflight warning (mode=%s): %s",
            normalized_mode,
            exc,
        )


def validate_market_runtime(cfg: Settings) -> None:
    _validate_market_registry_contract(
        cfg,
        context="runtime",
        record_snapshot=False,
        force_refresh=True,
    )


def _dispatch_order_rule_resolution_operator_event(resolution: object) -> None:
    event = getattr(resolution, "operator_event", None)
    if not isinstance(event, dict) or not event:
        return
    event_name = str(event.get("event_type") or event.get("event_name") or "").strip()
    if not event_name:
        return
    fields = {key: value for key, value in event.items() if key not in {"event_type", "event_name", "event_hash"}}
    from .operator_notification_service import OperatorNotificationService

    OperatorNotificationService().send_event(event_name, **fields)


def validate_live_mode_preflight(cfg: Settings) -> None:
    if cfg.MODE != "live":
        return
    # Validate the local storage boundary before reporting that no broker is
    # installed.  A live configuration with repository-local or paper-scoped
    # state is unsafe independently of broker availability and must surface
    # the actionable path violation first.
    try:
        db_path = getattr(cfg, "DB_PATH", None)
        run_lock_path = os.getenv("RUN_LOCK_PATH") or getattr(cfg, "RUN_LOCK_PATH", None)
        # Production Settings always carry both paths.  Keep their full
        # fail-fast validation, while allowing minimal capability probes to
        # reach the explicit unavailable-broker outcome instead of crashing
        # on an unrelated missing attribute.
        if db_path is not None or run_lock_path is not None:
            manager = PathManager.from_env(PROJECT_ROOT)
            validate_runtime_root_separation(manager.config)
        if db_path is not None:
            _validate_live_db_path_policy(str(db_path))
        if run_lock_path is not None:
            resolve_run_lock_path(str(run_lock_path), mode="live")
    except PathPolicyError as exc:
        raise LiveModeValidationError(str(exc)) from exc
    raise LiveModeValidationError("live execution is unavailable because no broker is configured (reason_code=LIVE_BROKER_NOT_CONFIGURED)")


def validate_live_real_order_execution_preflight(cfg: Settings) -> None:
    """Validate that the live run loop is configured for real submission."""
    if cfg.MODE != "live":
        return
    issues: list[str] = []
    if bool(cfg.LIVE_DRY_RUN):
        issues.append(
            "LIVE_DRY_RUN=false is required for MODE=live run; "
            "MODE=live `run` is real-order only. Current config is live dry-run/unarmed. "
            "Use `operation live-dry-run --short ... --long ...` to validate live decision flow "
            "without submitting orders"
        )
    if not bool(cfg.LIVE_REAL_ORDER_ARMED):
        issues.append(
            "LIVE_REAL_ORDER_ARMED=true is required for MODE=live run; "
            "unarmed live execution cannot start the real-order trading loop. "
            "Only use `run` after real-order arming, performance gate approval, and startup safety checks"
        )
    submit_authority_policy = submit_authority_policy_from_settings(cfg)
    if (
        submit_authority_policy.live_real_order_requires_target_delta
        and str(cfg.EXECUTION_ENGINE) != "target_delta"
    ):
        issues.append("live_real_order_requires_execution_engine_target_delta")
    if issues:
        raise LiveModeValidationError(
            "live real-order execution preflight failed: " + "; ".join(issues)
        )
    profile_report = validate_runtime_profile_bindings_for_live_startup(
        cfg,
        expected_profile_modes={"small_live"},
    )
    if not profile_report.ok:
        raise LiveModeValidationError(
            "live real-order execution preflight failed: " + "; ".join(profile_report.issues)
        )


def validate_live_dry_run_loop_startup_contract(cfg: Settings) -> None:
    """Validate that the live dry-run loop is explicitly unarmed and no-submit."""
    issues: list[str] = []
    if cfg.MODE != "live":
        issues.append(f"MODE=live is required for live-dry-run (got MODE={cfg.MODE})")
    if not bool(cfg.LIVE_DRY_RUN):
        issues.append("LIVE_DRY_RUN=true is required for live-dry-run")
    if bool(cfg.LIVE_REAL_ORDER_ARMED):
        issues.append("LIVE_REAL_ORDER_ARMED=false is required for live-dry-run")
    if issues:
        raise LiveModeValidationError(
            "live dry-run loop startup contract failed: " + "; ".join(issues)
        )
    validate_live_mode_preflight(cfg)
    profile_report = validate_runtime_profile_bindings_for_live_startup(
        cfg,
        expected_profile_modes={"live_dry_run"},
    )
    if not profile_report.ok:
        raise LiveModeValidationError(
            "live dry-run loop startup contract failed: " + "; ".join(profile_report.issues)
        )


def validate_live_run_startup_contract(
    cfg: Settings,
    *,
    code_provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Single startup gate for live run-loop execution."""
    validate_live_mode_preflight(cfg)
    validate_live_real_order_execution_preflight(cfg)
    provenance = validate_runtime_code_provenance_for_live_real_order(
        cfg,
        code_provenance=code_provenance,
    )
    if not bool(provenance.get("ok")):
        raise LiveModeValidationError(
            "live run startup contract failed: "
            f"reason_code={provenance.get('reason_code')}"
        )
    return {
        "startup_contract_artifact_type": "live_run_startup_contract",
        **provenance,
    }


def _git_output(args: tuple[str, ...], *, cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    return completed.stdout.strip()


@lru_cache(maxsize=1)
def runtime_code_provenance() -> dict[str, object]:
    """Return redacted code identity suitable for logs and submit evidence."""
    env_commit = str(os.getenv("OPERATION_DEPLOY_COMMIT_SHA") or "").strip()
    env_dirty = str(os.getenv("OPERATION_DEPLOY_DIRTY") or "").strip().lower()
    if env_commit:
        return {
            "commit_sha": env_commit,
            "working_tree_dirty": env_dirty in {"1", "true", "yes", "y", "on"},
            "source": "env",
            "git_available": False,
            "runtime_git_diff_hash": str(os.getenv("OPERATION_RUNTIME_GIT_DIFF_HASH") or "").strip(),
            "runtime_git_diff_artifact_path": str(os.getenv("OPERATION_RUNTIME_GIT_DIFF_ARTIFACT_PATH") or "").strip(),
            "source_archive_hash": str(os.getenv("OPERATION_SOURCE_ARCHIVE_HASH") or "").strip(),
            "operator_dirty_runtime_ack": str(os.getenv("OPERATION_OPERATOR_DIRTY_RUNTIME_ACK") or "").strip(),
        }

    commit_sha = _git_output(("rev-parse", "HEAD"), cwd=PROJECT_ROOT)
    dirty_probe = _git_output(("status", "--porcelain"), cwd=PROJECT_ROOT)
    return {
        "commit_sha": commit_sha,
        "working_tree_dirty": bool(dirty_probe) if dirty_probe is not None else None,
        "source": "git" if commit_sha else "unavailable",
        "git_available": bool(commit_sha),
        "runtime_git_diff_hash": str(os.getenv("OPERATION_RUNTIME_GIT_DIFF_HASH") or "").strip(),
        "runtime_git_diff_artifact_path": str(os.getenv("OPERATION_RUNTIME_GIT_DIFF_ARTIFACT_PATH") or "").strip(),
        "source_archive_hash": str(os.getenv("OPERATION_SOURCE_ARCHIVE_HASH") or "").strip(),
        "operator_dirty_runtime_ack": str(os.getenv("OPERATION_OPERATOR_DIRTY_RUNTIME_ACK") or "").strip(),
    }


def validate_runtime_code_provenance_for_live_real_order(
    cfg: Settings,
    *,
    code_provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    provenance = dict(code_provenance or runtime_code_provenance())
    real_order = (
        str(getattr(cfg, "MODE", "") or "").strip().lower() == "live"
        and bool(getattr(cfg, "LIVE_REAL_ORDER_ARMED", False))
        and not bool(getattr(cfg, "LIVE_DRY_RUN", True))
    )
    dirty = provenance.get("working_tree_dirty") is True
    diff_hash = str(provenance.get("runtime_git_diff_hash") or "").strip()
    diff_path = str(provenance.get("runtime_git_diff_artifact_path") or "").strip()
    archive_hash = str(provenance.get("source_archive_hash") or "").strip()
    ack = str(provenance.get("operator_dirty_runtime_ack") or "").strip()
    ok = True
    reason = "OK"
    if real_order and dirty and not (diff_hash and diff_path and archive_hash and ack):
        ok = False
        reason = "DIRTY_RUNTIME_PROVENANCE_MISSING_DIFF_ARTIFACT"
    return {
        "ok": ok,
        "reason_code": reason,
        "live_real_order": real_order,
        "runtime_git_commit_sha": str(provenance.get("commit_sha") or ""),
        "runtime_git_dirty": dirty,
        "runtime_git_diff_hash": diff_hash,
        "runtime_git_diff_artifact_path": diff_path,
        "source_archive_hash": archive_hash,
        "operator_dirty_runtime_ack": ack,
    }


def _safe_secret_hash_prefix(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _bot_related_env_keys() -> set[str]:
    prefixes = (
        "OPERATION_",
        "LIVE_",
        "PAPER_",
        "STRATEGY_",
        "EXECUTION_",
        "NOTIFIER_",
        "NTFY_",
        "TELEGRAM_",
        "SLACK_",
        "MARKET_",
        "MAX_",
        "MIN_",
        "DB_",
        "RUN_",
        "DATA_",
        "LOG_",
        "BACKUP_",
        "ARCHIVE_",
        "ENV_",
        "TARGET_",
        "RESIDUAL_",
    )
    exact = {"MODE", "PAIR", "MARKET", "INTERVAL", "EVERY", "FEE_RATE", "BUY_FRACTION", "KILL_SWITCH"}
    return {key for key in os.environ if key in exact or key.startswith(prefixes)}


def config_contract_metadata(cfg: Settings) -> dict[str, object]:
    settings_fields = {field.name for field in fields(Settings)}
    declared_keys = set(SPEC_BY_NAME)
    explicit_keys = sorted(key for key in declared_keys if os.getenv(key) not in (None, ""))
    defaulted_keys = sorted(key for key in settings_fields & declared_keys if os.getenv(key) in (None, ""))
    deprecated_env_keys = sorted(
        key for key in explicit_keys if SPEC_BY_NAME[key].deprecated or SPEC_BY_NAME[key].ignored
    )
    unknown_env_keys = sorted(_bot_related_env_keys() - declared_keys)
    secret_keys = {spec.name for spec in ENV_SPECS if spec.secret}
    effective_payload: dict[str, object] = {}
    for key in sorted(settings_fields & declared_keys):
        value = getattr(cfg, key)
        if key in secret_keys:
            effective_payload[key] = {
                "present": bool(str(value or "").strip()),
                "length": len(str(value or "")),
                "hash_prefix": _safe_secret_hash_prefix(value),
            }
        else:
            effective_payload[key] = value
    encoded = json.dumps(effective_payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    docs_path = PROJECT_ROOT / "docs" / "config-reference.md"
    env_example_path = PROJECT_ROOT / ".env.example"
    return {
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "config_spec_hash": config_spec_hash(),
        "settings_effective_hash": "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "settings_defaulted_keys": defaulted_keys,
        "settings_explicit_keys": explicit_keys,
        "unknown_env_keys": unknown_env_keys,
        "deprecated_env_keys": deprecated_env_keys,
        "generated_docs_hash": artifact_hash(docs_path),
        "env_example_hash": artifact_hash(env_example_path),
    }


def _env_file_contract_metadata(env_summary: dict[str, object]) -> dict[str, object]:
    enriched = dict(env_summary or {})
    env_file = str(enriched.get("env_file") or "").strip()
    if not env_file:
        enriched.setdefault("mtime_ns", None)
        enriched.setdefault("inode", None)
        enriched.setdefault("size_bytes", None)
        enriched.setdefault("content_hash_prefix", "")
        return enriched
    try:
        path = Path(env_file).expanduser()
        stat = path.stat()
        enriched["mtime_ns"] = int(stat.st_mtime_ns)
        enriched["inode"] = int(stat.st_ino)
        enriched["size_bytes"] = int(stat.st_size)
        enriched["content_hash_prefix"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except Exception:
        enriched.setdefault("mtime_ns", None)
        enriched.setdefault("inode", None)
        enriched.setdefault("size_bytes", None)
        enriched.setdefault("content_hash_prefix", "")
    return enriched


def _config_lint_finding(kind: str, *, legacy: str, details: dict[str, object] | None = None) -> dict[str, object]:
    message = CONFIG_LINT_MESSAGES[kind]
    return {
        "reason_code": message.reason_code,
        "severity": message.severity,
        "message": message.message,
        "recommended_action": message.recommended_action,
        "docs_hint": message.docs_hint,
        "legacy_text": legacy,
        "details": details or {},
    }


def live_env_contract_lint_findings(cfg: Settings) -> tuple[dict[str, object], ...]:
    if str(cfg.MODE or "").strip().lower() != "live":
        return ()
    findings: list[dict[str, object]] = []
    try:
        strategy_set = _resolve_runtime_strategy_set_for_live_startup(cfg)
        selection_kind = _runtime_selection_kind(strategy_set)
    except Exception:
        selection_kind = "single_strategy"
    profile_path = str(cfg.OPERATION_APPROVAL_PATH or "").strip()
    if profile_path.startswith("<") and profile_path.endswith(">"):
        findings.append(
            _config_lint_finding(
                "approved_profile_placeholder",
                legacy="approved_profile_placeholder",
                details={"key": "OPERATION_APPROVAL_PATH"},
            )
        )
    deprecated_keys = [
        key
        for key in (
            "BUY_PRICE_NONE_MARKET_TO_PRICE_ALIAS_ENABLED",
            "LIVE_ALLOW_ORDER_RULE_FALLBACK",
        )
        if os.getenv(key) not in (None, "")
    ]
    for key in deprecated_keys:
        findings.append(
            _config_lint_finding(
                "deprecated_ignored_env_key",
                legacy=f"deprecated_ignored_env_key:{key}",
                details={"key": key},
            )
        )
    secret_keys = ("SLACK_WEBHOOK_URL",)
    for key in secret_keys:
        raw = os.getenv(key)
        if raw is not None and raw != raw.strip():
            findings.append(_config_lint_finding("secret_bearing_key_has_surrounding_whitespace", legacy=f"secret_bearing_key_has_surrounding_whitespace:{key}", details={"key": key, "value_present": True, "value_length": len(raw)}))
    paper_keys = [key for key in PAPER_ONLY_ENV_KEYS if os.getenv(key) not in (None, "")]
    for key in paper_keys:
        findings.append(
            _config_lint_finding(
                "paper_only_key_in_live_env",
                legacy=f"paper_only_key_in_live_env:{key}",
                details={"key": key},
            )
        )
    try:
        if int(cfg.MAX_DAILY_ORDER_COUNT) >= 500:
            findings.append(
                _config_lint_finding(
                    "risky_live_limit",
                    legacy="risky_live_limit:MAX_DAILY_ORDER_COUNT>=500",
                    details={"key": "MAX_DAILY_ORDER_COUNT", "threshold": 500, "value": int(cfg.MAX_DAILY_ORDER_COUNT)},
                )
            )
    except (TypeError, ValueError):
        pass
    if selection_kind == "single_strategy" and not profile_path:
        findings.append(
            _config_lint_finding(
                "approved_profile_not_configured",
                legacy="approved_profile_not_configured",
                details={"key": "OPERATION_APPROVAL_PATH"},
            )
        )
    explicit_env_file = str(os.getenv("OPERATION_ENV_FILE") or "").strip()
    live_env_file = str(os.getenv("OPERATION_ENV_FILE_LIVE") or "").strip()
    if explicit_env_file and live_env_file and explicit_env_file != live_env_file:
        findings.append(
            _config_lint_finding(
                "live_env_file_source_mismatch",
                legacy="live_env_file_source_mismatch:OPERATION_ENV_FILE!=OPERATION_ENV_FILE_LIVE",
                details={"source_key": "OPERATION_ENV_FILE", "live_key": "OPERATION_ENV_FILE_LIVE"},
            )
        )
    for key in sorted(_bot_related_env_keys() - set(SPEC_BY_NAME)):
        findings.append(
            _config_lint_finding(
                "unknown_bot_related_env_key",
                legacy=f"unknown_bot_related_env_key:{key}",
                details={"key": key},
            )
        )
    return tuple(findings)


def live_env_contract_lints(cfg: Settings) -> tuple[str, ...]:
    return tuple(str(finding["legacy_text"]) for finding in live_env_contract_lint_findings(cfg))


def live_execution_contract_summary(
    cfg: Settings,
    *,
    env_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    managed_roots = {
        key: str(Path(os.getenv(key, "")).expanduser()) if os.getenv(key) else ""
        for key in ("ENV_ROOT", "RUN_ROOT", "DATA_ROOT", "LOG_ROOT", "BACKUP_ROOT", "ARCHIVE_ROOT")
    }
    runtime_paths = {
        "DB_PATH": str(os.getenv("DB_PATH") or cfg.DB_PATH or ""),
        "RUN_LOCK_PATH": str(os.getenv("RUN_LOCK_PATH") or cfg.RUN_LOCK_PATH or ""),
    }
    explicit_env = dict(env_summary or {})
    explicit_env_file = _env_file_contract_metadata(explicit_env)
    profile_binding_report = None
    profile_binding_summary: dict[str, object]
    try:
        profile_binding_report = validate_runtime_profile_bindings_for_live_startup(cfg)
        profile_bindings = [
            {
                key: value
                for key, value in binding.__dict__.items()
            }
            for binding in profile_binding_report.bindings
        ]
        profile_binding_summary = {
            "runtime_selection_kind": profile_binding_report.selection_kind,
            "runtime_strategy_set_source": profile_binding_report.runtime_strategy_set_source,
            "profile_binding_kind": (
                "spec_bound_approved_profiles"
                if profile_binding_report.selection_kind == "multi_strategy"
                else "global_approved_profile_selector"
            ),
            "startup_gate_authority": (
                "RUNTIME_STRATEGY_SET_JSON"
                if profile_binding_report.selection_kind == "multi_strategy"
                else "STRATEGY_NAME"
            ),
            "global_profile_selector_present": profile_binding_report.global_profile_selector_present,
            "ok": profile_binding_report.ok,
            "issues": list(profile_binding_report.issues),
            "bindings": profile_bindings,
            "strategy_instance_approved_profile_hashes": [
                item.get("profile_hash") for item in profile_bindings if item.get("profile_hash")
            ],
        }
    except Exception as exc:
        profile_binding_summary = {
            "runtime_selection_kind": "unknown",
            "runtime_strategy_set_source": "",
            "profile_binding_kind": "unresolved",
            "startup_gate_authority": "unresolved",
            "global_profile_selector_present": bool(_global_approved_profile_selector(cfg)),
            "ok": False,
            "issues": [f"{type(exc).__name__}:{exc}"],
            "bindings": [],
            "strategy_instance_approved_profile_hashes": [],
        }
    if profile_binding_report is not None and profile_binding_report.selection_kind == "multi_strategy":
        first_binding = profile_binding_report.bindings[0] if profile_binding_report.bindings else None
        approved_profile_summary = {
            "approved_profile_verification_ok": profile_binding_report.ok,
            "approved_profile_block_reason": ";".join(profile_binding_report.issues) if profile_binding_report.issues else "",
            "approved_profile_contract_scope": "runtime_strategy_set_spec_bound_profiles",
            "approved_profile_path": getattr(first_binding, "profile_path", None),
            "approved_profile_hash": getattr(first_binding, "profile_hash", None),
            "legacy_candidate_profile_path_used": False,
            "legacy_compatibility_used": False,
        }
    else:
        runtime_contract = runtime_contract_from_settings(cfg)
        expected_profile_modes, mode_reason = expected_profile_modes_for_runtime(runtime_contract)
        profile_result = verify_profile_against_runtime(
            profile_path=str(runtime_contract.get("profile_selector") or "").strip(),
            runtime=runtime_contract,
            require_profile=False,
            expected_profile_modes=expected_profile_modes,
            expected_profile_mode_reason=mode_reason,
            verify_source_promotion=True,
        )
        approved_profile_summary = profile_result.audit_fields()
    config_contract = config_contract_metadata(cfg)
    submit_authority_policy = submit_authority_policy_from_settings(cfg)
    code_provenance = runtime_code_provenance()
    provenance_gate = validate_runtime_code_provenance_for_live_real_order(
        cfg,
        code_provenance=code_provenance,
    )
    if not provenance_gate["ok"]:
        raise RuntimeError(str(provenance_gate["reason_code"]))
    return {
        "mode": cfg.MODE,
        "pair": cfg.PAIR,
        "live_dry_run": bool(cfg.LIVE_DRY_RUN),
        "live_real_order_armed": bool(cfg.LIVE_REAL_ORDER_ARMED),
        "live_submit_contract_profile": str(cfg.LIVE_SUBMIT_CONTRACT_PROFILE),
        **submit_authority_policy.as_dict(),
        "submit_authority_policy_hash": submit_authority_policy.content_hash(),
        "live_order_rule_fallback_profile": str(cfg.LIVE_ORDER_RULE_FALLBACK_PROFILE),
        "code_provenance": code_provenance,
        "runtime_code_provenance_gate": provenance_gate,
        "approved_profile": approved_profile_summary,
        "runtime_profile_binding": profile_binding_summary,
        "runtime_selection_kind": profile_binding_summary.get("runtime_selection_kind"),
        "runtime_strategy_set_source": profile_binding_summary.get("runtime_strategy_set_source"),
        "profile_binding_kind": profile_binding_summary.get("profile_binding_kind"),
        "global_profile_selector_present": profile_binding_summary.get("global_profile_selector_present"),
        "startup_gate_authority": profile_binding_summary.get("startup_gate_authority"),
        "explicit_env": explicit_env,
        "explicit_env_file": explicit_env_file,
        "live_env_contract_lints": list(live_env_contract_lints(cfg)),
        "live_env_contract_lint_findings": list(live_env_contract_lint_findings(cfg)),
        "config_contract": config_contract,
        "managed_roots": managed_roots,
        "runtime_paths": runtime_paths,
    }


def live_execution_contract_fingerprint(summary: dict[str, object]) -> str:
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def log_live_execution_contract(
    cfg: Settings,
    *,
    caller: str,
    env_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    from .observability import format_log_kv

    summary = live_execution_contract_summary(cfg, env_summary=env_summary)
    if cfg.MODE != "live":
        return summary
    roots = summary.get("managed_roots") if isinstance(summary.get("managed_roots"), dict) else {}
    paths = summary.get("runtime_paths") if isinstance(summary.get("runtime_paths"), dict) else {}
    explicit_env = summary.get("explicit_env") if isinstance(summary.get("explicit_env"), dict) else {}
    explicit_env_file = summary.get("explicit_env_file") if isinstance(summary.get("explicit_env_file"), dict) else {}
    code_provenance = summary.get("code_provenance") if isinstance(summary.get("code_provenance"), dict) else {}
    approved_profile = summary.get("approved_profile") if isinstance(summary.get("approved_profile"), dict) else {}
    runtime_profile_binding = (
        summary.get("runtime_profile_binding")
        if isinstance(summary.get("runtime_profile_binding"), dict)
        else {}
    )
    config_contract = summary.get("config_contract") if isinstance(summary.get("config_contract"), dict) else {}
    lint_findings = summary.get("live_env_contract_lint_findings") or []
    lint_reason_codes = [
        str(item.get("reason_code"))
        for item in lint_findings
        if isinstance(item, dict) and item.get("reason_code")
    ]
    logging.getLogger("operation.run").info(
        format_log_kv(
            "[LIVE_EXECUTION_CONTRACT]",
            caller=caller,
            fingerprint=live_execution_contract_fingerprint(summary),
            mode=summary.get("mode"),
            pair=summary.get("pair"),
            live_dry_run=1 if bool(summary.get("live_dry_run")) else 0,
            live_real_order_armed=1 if bool(summary.get("live_real_order_armed")) else 0,
            runtime_selection_kind=summary.get("runtime_selection_kind"),
            runtime_strategy_set_source=summary.get("runtime_strategy_set_source"),
            profile_binding_kind=summary.get("profile_binding_kind"),
            global_profile_selector_present=(
                1 if bool(summary.get("global_profile_selector_present")) else 0
            ),
            startup_gate_authority=summary.get("startup_gate_authority"),
            strategy_instance_approved_profile_hashes=",".join(
                str(item)
                for item in runtime_profile_binding.get("strategy_instance_approved_profile_hashes", [])
            )
            or "none",
            live_submit_contract_profile=summary.get("live_submit_contract_profile"),
            live_order_rule_fallback_profile=summary.get("live_order_rule_fallback_profile"),
            api_base=summary.get("api_base"),
            api_key_present=1 if bool(summary.get("api_key_present")) else 0,
            api_key_length=summary.get("api_key_length"),
            api_key_hash_prefix=summary.get("api_key_hash_prefix"),
            api_secret_present=1 if bool(summary.get("api_secret_present")) else 0,
            api_secret_length=summary.get("api_secret_length"),
            api_secret_hash_prefix=summary.get("api_secret_hash_prefix"),
            code_commit_sha=code_provenance.get("commit_sha"),
            code_working_tree_dirty=code_provenance.get("working_tree_dirty"),
            code_provenance_source=code_provenance.get("source"),
            approved_profile_path=approved_profile.get("approved_profile_path"),
            approved_profile_hash=approved_profile.get("approved_profile_hash"),
            approved_profile_mode=approved_profile.get("approved_profile_mode"),
            approved_profile_verification_ok=approved_profile.get("approved_profile_verification_ok"),
            approved_profile_block_reason=approved_profile.get("approved_profile_block_reason"),
            approved_profile_loaded=approved_profile.get("approved_profile_loaded"),
            approved_profile_schema_hash_valid=approved_profile.get("approved_profile_schema_hash_valid"),
            approved_profile_source_verified=approved_profile.get("approved_profile_source_verified"),
            approved_profile_evidence_verified=approved_profile.get("approved_profile_evidence_verified"),
            approved_profile_runtime_verified=approved_profile.get("approved_profile_runtime_verified"),
            approved_profile_contract_scope=approved_profile.get("approved_profile_contract_scope"),
            legacy_candidate_profile_path_used=approved_profile.get("legacy_candidate_profile_path_used"),
            source_promotion_artifact_path=approved_profile.get("source_promotion_artifact_path"),
            promotion_content_hash=approved_profile.get("promotion_content_hash"),
            lineage_hash=approved_profile.get("lineage_hash"),
            legacy_compatibility_used=approved_profile.get("legacy_compatibility_used"),
            candidate_profile_hash=approved_profile.get("candidate_profile_hash"),
            manifest_hash=approved_profile.get("manifest_hash"),
            dataset_content_hash=approved_profile.get("dataset_content_hash"),
            paper_validation_evidence_path=approved_profile.get("paper_validation_evidence_path"),
            paper_validation_evidence_content_hash=approved_profile.get("paper_validation_evidence_content_hash"),
            live_readiness_evidence_path=approved_profile.get("live_readiness_evidence_path"),
            live_readiness_evidence_content_hash=approved_profile.get("live_readiness_evidence_content_hash"),
            decision_equivalence_report_path=approved_profile.get("decision_equivalence_report_path"),
            decision_equivalence_content_hash=approved_profile.get("decision_equivalence_content_hash"),
            env_source_key=explicit_env.get("source_key"),
            env_file=explicit_env.get("env_file"),
            env_loaded=explicit_env.get("loaded"),
            env_exists=explicit_env.get("exists"),
            env_override=explicit_env.get("override"),
            env_file_mtime_ns=explicit_env_file.get("mtime_ns"),
            env_file_inode=explicit_env_file.get("inode"),
            env_file_hash_prefix=explicit_env_file.get("content_hash_prefix"),
            live_env_contract_lints=",".join(str(item) for item in summary.get("live_env_contract_lints") or []) or "none",
            live_env_contract_lint_reason_codes=",".join(lint_reason_codes) or "none",
            config_schema_version=config_contract.get("config_schema_version"),
            config_spec_hash=config_contract.get("config_spec_hash"),
            settings_effective_hash=config_contract.get("settings_effective_hash"),
            settings_explicit_keys=",".join(str(item) for item in config_contract.get("settings_explicit_keys") or []) or "none",
            settings_defaulted_keys=",".join(str(item) for item in config_contract.get("settings_defaulted_keys") or []) or "none",
            unknown_env_keys=",".join(str(item) for item in config_contract.get("unknown_env_keys") or []) or "none",
            deprecated_env_keys=",".join(str(item) for item in config_contract.get("deprecated_env_keys") or []) or "none",
            generated_docs_hash=config_contract.get("generated_docs_hash"),
            env_example_hash=config_contract.get("env_example_hash"),
            env_root=roots.get("ENV_ROOT"),
            run_root=roots.get("RUN_ROOT"),
            data_root=roots.get("DATA_ROOT"),
            log_root=roots.get("LOG_ROOT"),
            backup_root=roots.get("BACKUP_ROOT"),
            archive_root=roots.get("ARCHIVE_ROOT"),
            db_path=paths.get("DB_PATH"),
            run_lock_path=paths.get("RUN_LOCK_PATH"),
        )
    )
    return summary
