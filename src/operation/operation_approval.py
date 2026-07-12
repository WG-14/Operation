"""Operation-owned strategy approval artifact and fail-closed runtime binding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .artifact_hashing import sha256_prefixed
from .execution_reality_contract import build_execution_reality_contract
from .operation_strategy.registry import (
    OperationStrategyRegistryError,
    operation_exit_policy_materialization_from_parameters,
    resolve_operation_strategy_plugin,
)
from .operation_strategy.spec import (
    materialize_strategy_parameters,
    materialized_strategy_parameters_hash,
)
from .paths import PathManager
from .risk_contract import RiskPolicy
from .storage_io import write_json_atomic


OPERATION_APPROVAL_SCHEMA_VERSION = 1
OPERATION_APPROVAL_HASH_FIELD = "content_hash"
OPERATION_APPROVAL_SELECTOR_ENV = "OPERATION_APPROVAL_PATH"
OPERATION_APPROVAL_ALLOWED_MODES = frozenset({"paper", "live_dry_run", "small_live"})
PROFILE_RUNTIME_COST_MISMATCH_ACTION = "Operation approval/runtime cost drift requires operator review."


class OperationApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class OperationApprovalVerificationResult:
    ok: bool
    reason: str
    approval_path: str | None
    approval_hash: str | None
    allowed_mode: str | None
    mismatches: tuple[dict[str, object], ...]
    approval: dict[str, Any] | None = None

    @property
    def profile_path(self) -> str | None:
        return self.approval_path

    @property
    def profile_hash(self) -> str | None:
        return self.approval_hash

    @property
    def mode(self) -> str | None:
        return self.allowed_mode

    def audit_fields(self) -> dict[str, object]:
        return {
            "operation_approval_path": self.approval_path,
            "operation_approval_hash": self.approval_hash,
            "operation_approval_allowed_mode": self.allowed_mode,
            "operation_approval_verification_ok": self.ok,
            "operation_approval_block_reason": self.reason,
            "operation_approval_mismatch_count": len(self.mismatches),
            "operation_approval_mismatches": [dict(item) for item in self.mismatches],
        }


def operation_approval_path_from_env() -> str:
    return str(__import__("os").getenv(OPERATION_APPROVAL_SELECTOR_ENV, "") or "").strip()


def compute_file_content_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _hash_payload(approval: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in approval.items() if key != OPERATION_APPROVAL_HASH_FIELD}


def compute_operation_approval_hash(approval: Mapping[str, Any]) -> str:
    return sha256_prefixed(_hash_payload(approval))


def resolve_operation_artifact_path(
    path: str | Path,
    *,
    manager: PathManager | None = None,
    label: str = "operation_approval",
    must_exist: bool = True,
) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise OperationApprovalError(f"{label}_path_missing")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise OperationApprovalError(f"{label}_path_must_be_absolute")
    resolved = candidate.resolve()
    project_root = (manager.project_root if manager is not None else Path.cwd()).resolve()
    if PathManager._is_within(resolved, project_root):
        raise OperationApprovalError(f"{label}_path_repo_local_not_allowed")
    if must_exist and not resolved.exists():
        raise OperationApprovalError(f"{label}_path_not_found")
    if must_exist and not resolved.is_file():
        raise OperationApprovalError(f"{label}_path_not_file")
    return resolved


def _as_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OperationApprovalError(f"{field}_must_be_object")
    return {str(key): item for key, item in value.items()}


def _required_text(approval: Mapping[str, Any], field: str) -> str:
    value = str(approval.get(field) or "").strip()
    if not value:
        raise OperationApprovalError(f"{field}_missing")
    return value


def _approval_runtime_mode(runtime: Mapping[str, Any]) -> str:
    mode = str(runtime.get("mode") or "").strip().lower()
    if mode == "paper":
        return "paper"
    if mode != "live":
        raise OperationApprovalError("runtime_mode_invalid")
    dry_run = _as_bool(runtime.get("live_dry_run"))
    armed = _as_bool(runtime.get("live_real_order_armed"))
    if dry_run and armed:
        raise OperationApprovalError("live_mode_arming_flags_ambiguous")
    if dry_run:
        return "live_dry_run"
    if armed:
        return "small_live"
    raise OperationApprovalError("live_mode_not_dry_run_or_armed")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return _as_bool(left) == _as_bool(right)
    try:
        return abs(float(left) - float(right)) <= 1e-12
    except (TypeError, ValueError):
        return left == right


def _parse_expiry(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise OperationApprovalError("expires_at_missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperationApprovalError("expires_at_invalid") from exc
    if parsed.tzinfo is None:
        raise OperationApprovalError("expires_at_timezone_required")
    return parsed.astimezone(timezone.utc)


def validate_operation_approval(approval: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(approval)
    if int(payload.get("schema_version") or 0) != OPERATION_APPROVAL_SCHEMA_VERSION:
        raise OperationApprovalError("schema_version_unsupported")
    for field in (
        "strategy_name", "strategy_version", "strategy_spec_hash", "strategy_plugin_contract_hash",
        "market", "interval", "strategy_parameters_hash", "exit_policy_hash", "risk_policy_hash",
        "execution_contract_hash", "approved_by", "approved_at", "expires_at", "content_hash",
    ):
        _required_text(payload, field)
    parameters = _as_mapping(payload.get("strategy_parameters"), field="strategy_parameters")
    risk_policy = _as_mapping(payload.get("risk_policy"), field="risk_policy")
    allowed_modes = payload.get("allowed_modes")
    if not isinstance(allowed_modes, list) or not allowed_modes:
        raise OperationApprovalError("allowed_modes_missing")
    normalized_modes = {str(item).strip() for item in allowed_modes}
    if not normalized_modes <= OPERATION_APPROVAL_ALLOWED_MODES:
        raise OperationApprovalError("allowed_modes_invalid")
    max_order = payload.get("max_order_krw")
    try:
        if float(max_order) <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise OperationApprovalError("max_order_krw_invalid") from exc
    if materialized_strategy_parameters_hash(parameters) != payload["strategy_parameters_hash"]:
        raise OperationApprovalError("strategy_parameters_hash_mismatch")
    policy_hash = RiskPolicy(**{
        "schema_version": int(risk_policy.get("schema_version", 1) or 1),
        "max_daily_loss_krw": float(risk_policy.get("max_daily_loss_krw", 0.0) or 0.0),
        "max_position_loss_pct": float(risk_policy.get("max_position_loss_pct", 0.0) or 0.0),
        "max_daily_order_count": int(risk_policy.get("max_daily_order_count", 0) or 0),
        "max_trade_count_per_day": int(risk_policy.get("max_trade_count_per_day", 0) or 0),
        "max_drawdown_pct": float(risk_policy.get("max_drawdown_pct", 0.0) or 0.0),
        "cooldown_after_loss_min": int(risk_policy.get("cooldown_after_loss_min", 0) or 0),
        "kill_switch": bool(risk_policy.get("kill_switch", False)),
        "max_open_positions": int(risk_policy.get("max_open_positions", 1) or 1),
        "unresolved_order_policy": str(risk_policy.get("unresolved_order_policy", "block") or "block"),
        "policy_status": str(risk_policy.get("policy_status", "enabled") or "enabled"),
        "missing_policy": str(risk_policy.get("missing_policy", "fail_closed_for_live") or "fail_closed_for_live"),
        "source": str(risk_policy.get("source", "operation_approval") or "operation_approval"),
    }).policy_hash()
    if policy_hash != payload["risk_policy_hash"]:
        raise OperationApprovalError("risk_policy_hash_mismatch")
    if compute_operation_approval_hash(payload) != payload[OPERATION_APPROVAL_HASH_FIELD]:
        raise OperationApprovalError("content_hash_mismatch")
    _parse_expiry(payload["expires_at"])
    return payload


def _risk_policy_from_settings(cfg: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "max_daily_loss_krw": float(getattr(cfg, "MAX_DAILY_LOSS_KRW", 0.0)),
        "max_position_loss_pct": float(getattr(cfg, "MAX_POSITION_LOSS_PCT", 0.0)),
        "max_daily_order_count": int(getattr(cfg, "MAX_DAILY_ORDER_COUNT", 0)),
        "max_trade_count_per_day": int(getattr(cfg, "MAX_DAILY_ORDER_COUNT", 0)),
        "max_drawdown_pct": 0.0,
        "cooldown_after_loss_min": int(getattr(cfg, "COOLDOWN_MIN", 0)),
        "kill_switch": bool(getattr(cfg, "KILL_SWITCH", False)),
        "max_open_positions": int(getattr(cfg, "MAX_OPEN_POSITIONS", 1)),
        "unresolved_order_policy": "block",
        "policy_status": "enabled",
        "missing_policy": "fail_closed_for_live",
        "source": "operation_runtime_settings",
    }


def _execution_contract_from_settings(cfg: object) -> dict[str, Any]:
    policy = str(getattr(cfg, "EXECUTION_FILL_REFERENCE_POLICY", "") or "").strip()
    if not policy:
        raise OperationApprovalError("execution_contract_missing")
    required_top = bool(getattr(cfg, "EXECUTION_TOP_OF_BOOK_REQUIRED", False)) or policy in {
        "first_orderbook_after_decision", "latency_adjusted_orderbook",
    }
    return build_execution_reality_contract(
        fill_reference_policy=policy,
        decision_guard_ms=int(getattr(cfg, "EXECUTION_DECISION_GUARD_MS", 0)),
        max_quote_wait_ms=int(getattr(cfg, "EXECUTION_MAX_QUOTE_WAIT_MS", 0)),
        missing_quote_policy=str(getattr(cfg, "EXECUTION_MISSING_QUOTE_POLICY", "warn")),
        min_execution_reality_level_for_promotion=None,
        allow_same_candle_close_fill=bool(getattr(cfg, "EXECUTION_ALLOW_SAME_CANDLE_CLOSE_FILL", False)),
        quote_source=str(getattr(cfg, "EXECUTION_QUOTE_SOURCE", "") or "") or None,
        quote_age_limit_ms=getattr(cfg, "EXECUTION_QUOTE_AGE_LIMIT_MS", None),
        top_of_book_required=required_top, top_of_book_available=required_top,
        top_of_book_is_full_depth=bool(getattr(cfg, "EXECUTION_TOP_OF_BOOK_IS_FULL_DEPTH", False)),
        depth_required=bool(getattr(cfg, "EXECUTION_DEPTH_REQUIRED", False)),
        trade_tick_required=bool(getattr(cfg, "EXECUTION_TRADE_TICK_REQUIRED", False)),
        queue_position_required=bool(getattr(cfg, "EXECUTION_QUEUE_POSITION_REQUIRED", False)),
        market_impact_required=bool(getattr(cfg, "EXECUTION_MARKET_IMPACT_REQUIRED", False)),
        intra_candle_path_available=bool(getattr(cfg, "EXECUTION_INTRA_CANDLE_PATH_AVAILABLE", False)),
        latency_model={"type": str(getattr(cfg, "EXECUTION_LATENCY_MODEL_TYPE", "fixed")), "latency_ms": int(getattr(cfg, "EXECUTION_LATENCY_MS", 0))},
        partial_fill_model={"type": str(getattr(cfg, "EXECUTION_PARTIAL_FILL_MODEL_TYPE", "fixed")), "partial_fill_rate": float(getattr(cfg, "EXECUTION_PARTIAL_FILL_RATE", 0.0))},
        order_failure_model={"type": str(getattr(cfg, "EXECUTION_ORDER_FAILURE_MODEL_TYPE", "fixed")), "order_failure_rate": float(getattr(cfg, "EXECUTION_ORDER_FAILURE_RATE", 0.0))},
        fee_source=str(getattr(cfg, "EXECUTION_FEE_SOURCE", "") or "") or None,
        slippage_source=str(getattr(cfg, "EXECUTION_SLIPPAGE_SOURCE", "") or "") or None,
        calibration_required=bool(getattr(cfg, "EXECUTION_CALIBRATION_REQUIRED", False)),
        calibration_artifact_hash=str(getattr(cfg, "EXECUTION_CALIBRATION_ARTIFACT_HASH", "") or "") or None,
        execution_reality_level=str(getattr(cfg, "EXECUTION_REALITY_LEVEL", "") or "") or None,
    )


def runtime_contract_from_settings(cfg: object, *, strategy_name: str | None = None) -> dict[str, Any]:
    name = str(strategy_name or getattr(cfg, "STRATEGY_NAME", "") or "").strip()
    if not name:
        raise OperationApprovalError("runtime_strategy_name_required")
    try:
        plugin = resolve_operation_strategy_plugin(name)
    except OperationStrategyRegistryError as exc:
        raise OperationApprovalError(f"runtime_strategy_unsupported:{name}") from exc
    adapter = plugin.runtime_parameter_adapter
    if adapter is None:
        raise OperationApprovalError(f"runtime_parameter_adapter_missing:{name}")
    raw = dict(adapter.from_settings(cfg))
    parameters = materialize_strategy_parameters(
        name, raw,
        fee_rate=float(getattr(cfg, "LIVE_FEE_RATE_ESTIMATE", 0.0)),
        slippage_bps=float(getattr(cfg, "STRATEGY_ENTRY_SLIPPAGE_BPS", 0.0)),
    )
    exit_policy = operation_exit_policy_materialization_from_parameters(
        name, parameters, materialization_mode="operation_approval_runtime_contract"
    )
    risk_policy = _risk_policy_from_settings(cfg)
    risk_hash = RiskPolicy(**risk_policy).policy_hash()
    execution_contract = _execution_contract_from_settings(cfg)
    return {
        "mode": str(getattr(cfg, "MODE", "paper") or "paper"),
        "live_dry_run": bool(getattr(cfg, "LIVE_DRY_RUN", True)),
        "live_real_order_armed": bool(getattr(cfg, "LIVE_REAL_ORDER_ARMED", False)),
        "approval_selector": str(getattr(cfg, OPERATION_APPROVAL_SELECTOR_ENV, "") or ""),
        "strategy_name": name,
        "strategy_version": str(plugin.version),
        "strategy_spec_hash": plugin.spec.spec_hash(),
        "strategy_plugin_contract_hash": plugin.contract_hash(),
        "market": str(getattr(cfg, "PAIR", "") or ""),
        "interval": str(getattr(cfg, "INTERVAL", "") or ""),
        "strategy_parameters": parameters,
        "strategy_parameters_hash": materialized_strategy_parameters_hash(parameters),
        "exit_policy": dict(exit_policy.exit_policy),
        "exit_policy_hash": exit_policy.exit_policy_hash,
        "risk_policy": risk_policy,
        "risk_policy_hash": risk_hash,
        "execution_contract": execution_contract,
        "execution_contract_hash": execution_contract["execution_contract_hash"],
        "max_order_krw": float(getattr(cfg, "MAX_ORDER_KRW", 0.0)),
    }


def build_operation_approval(
    *, runtime: Mapping[str, Any], approved_by: str, expires_at: str,
    allowed_modes: list[str], max_order_krw: float | None = None,
    approved_at: str | None = None,
) -> dict[str, Any]:
    allowed = sorted({str(item).strip() for item in allowed_modes if str(item).strip()})
    if not allowed or not set(allowed) <= OPERATION_APPROVAL_ALLOWED_MODES:
        raise OperationApprovalError("allowed_modes_invalid")
    if not str(approved_by or "").strip():
        raise OperationApprovalError("approved_by_missing")
    _parse_expiry(expires_at)
    approval = {
        "schema_version": OPERATION_APPROVAL_SCHEMA_VERSION,
        "strategy_name": runtime["strategy_name"], "strategy_version": runtime["strategy_version"],
        "strategy_spec_hash": runtime["strategy_spec_hash"],
        "strategy_plugin_contract_hash": runtime["strategy_plugin_contract_hash"],
        "market": runtime["market"], "interval": runtime["interval"],
        "strategy_parameters": dict(runtime["strategy_parameters"]),
        "strategy_parameters_hash": runtime["strategy_parameters_hash"],
        "exit_policy_hash": runtime["exit_policy_hash"],
        "risk_policy": dict(runtime["risk_policy"]), "risk_policy_hash": runtime["risk_policy_hash"],
        "execution_contract_hash": runtime["execution_contract_hash"],
        "allowed_modes": allowed,
        "max_order_krw": float(runtime["max_order_krw"] if max_order_krw is None else max_order_krw),
        "approved_by": str(approved_by).strip(),
        "approved_at": approved_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "expires_at": expires_at,
    }
    approval[OPERATION_APPROVAL_HASH_FIELD] = compute_operation_approval_hash(approval)
    return validate_operation_approval(approval)


def load_operation_approval(path: str | Path, *, manager: PathManager | None = None) -> dict[str, Any]:
    resolved = resolve_operation_artifact_path(path, manager=manager)
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationApprovalError(f"operation_approval_unreadable:{exc}") from exc
    return validate_operation_approval(_as_mapping(payload, field="operation_approval"))


def write_operation_approval_atomic(path: str | Path, approval: Mapping[str, Any], *, manager: PathManager) -> Path:
    resolved = resolve_operation_artifact_path(path, manager=manager, must_exist=False)
    write_json_atomic(resolved, validate_operation_approval(approval))
    return resolved


def diff_operation_approval_to_runtime(approval: Mapping[str, Any], runtime: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    checked = validate_operation_approval(approval)
    mismatches: list[dict[str, object]] = []
    for field in (
        "strategy_name", "strategy_version", "strategy_spec_hash", "strategy_plugin_contract_hash",
        "market", "interval", "strategy_parameters_hash", "exit_policy_hash", "risk_policy_hash",
        "execution_contract_hash",
    ):
        if not _equal(checked.get(field), runtime.get(field)):
            mismatches.append({"field": field, "expected": checked.get(field), "actual": runtime.get(field)})
    expected_parameters = _as_mapping(checked["strategy_parameters"], field="strategy_parameters")
    actual_parameters = _as_mapping(runtime.get("strategy_parameters", {}), field="runtime_strategy_parameters")
    for key in sorted(set(expected_parameters) | set(actual_parameters)):
        if not _equal(expected_parameters.get(key), actual_parameters.get(key)):
            mismatches.append({"field": f"strategy_parameters.{key}", "expected": expected_parameters.get(key), "actual": actual_parameters.get(key)})
    if float(runtime.get("max_order_krw") or 0.0) > float(checked["max_order_krw"]):
        mismatches.append({"field": "max_order_krw", "expected_max": checked["max_order_krw"], "actual": runtime.get("max_order_krw")})
    mode = _approval_runtime_mode(runtime)
    if mode not in set(checked["allowed_modes"]):
        mismatches.append({"field": "allowed_mode", "expected": checked["allowed_modes"], "actual": mode})
    if _parse_expiry(checked["expires_at"]) <= datetime.now(timezone.utc):
        mismatches.append({"field": "expires_at", "expected": "future", "actual": checked["expires_at"]})
    return tuple(mismatches)


def verify_operation_approval_against_runtime(
    *, approval_path: str | Path | None, runtime: Mapping[str, Any], require_approval: bool,
    manager: PathManager | None = None,
) -> OperationApprovalVerificationResult:
    raw = str(approval_path or "").strip()
    if not raw:
        return OperationApprovalVerificationResult(False, "operation_approval_missing" if require_approval else "operation_approval_not_configured", None, None, None, ())
    try:
        approval = load_operation_approval(raw, manager=manager)
        mismatches = diff_operation_approval_to_runtime(approval, runtime)
        return OperationApprovalVerificationResult(
            not mismatches, "ok" if not mismatches else "operation_approval_runtime_mismatch",
            str(Path(raw).expanduser().resolve()), str(approval[OPERATION_APPROVAL_HASH_FIELD]),
            _approval_runtime_mode(runtime), mismatches, approval,
        )
    except OperationApprovalError as exc:
        return OperationApprovalVerificationResult(False, str(exc), str(Path(raw).expanduser().resolve()), None, None, ())


def expected_operation_approval_modes(runtime: Mapping[str, Any]) -> tuple[set[str] | None, str | None]:
    try:
        return {_approval_runtime_mode(runtime)}, None
    except OperationApprovalError as exc:
        return set(), str(exc)


# The following adapters preserve the internal call shape during the one-shot
# cutover.  They do not parse, convert, or accept a former approval artifact.
PROFILE_HASH_FIELD = OPERATION_APPROVAL_HASH_FIELD
ApprovedProfileError = OperationApprovalError
approved_profile_path_from_env = operation_approval_path_from_env
expected_profile_modes_for_runtime = expected_operation_approval_modes
load_approved_profile = load_operation_approval
diff_profile_to_runtime = diff_operation_approval_to_runtime


def verify_profile_against_runtime(
    *,
    profile_path: str | Path | None,
    runtime: Mapping[str, Any],
    require_profile: bool,
    expected_profile_modes: set[str] | None = None,
    expected_profile_mode_reason: str | None = None,
    verify_source_promotion: bool = False,
) -> OperationApprovalVerificationResult:
    del expected_profile_modes, expected_profile_mode_reason, verify_source_promotion
    return verify_operation_approval_against_runtime(
        approval_path=profile_path,
        runtime=runtime,
        require_approval=require_profile,
    )


def profile_runtime_cost_match_status(approval: Mapping[str, Any] | None, runtime: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(approval, Mapping):
        return {"status": "WARN", "reason": "operation_approval_not_loaded"}
    expected = float(approval.get("max_order_krw") or 0.0)
    actual = float(runtime.get("max_order_krw") or 0.0)
    if actual > expected:
        return {"status": "FAIL", "reason": "runtime_max_order_exceeds_operation_approval", "expected": expected, "actual": actual}
    return {"status": "PASS", "reason": "operation_approval_runtime_limits_match", "expected": expected, "actual": actual}
