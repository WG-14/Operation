from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bithumb_bot.cli.context import AppContext
from bithumb_bot.cli.main import main as cli_main
from bithumb_bot.operation_approval import (
    OperationApprovalError,
    build_operation_approval,
    compute_operation_approval_hash,
    diff_operation_approval_to_runtime,
    validate_operation_approval,
    verify_operation_approval_against_runtime,
    write_operation_approval_atomic,
)
from bithumb_bot.operation_strategy.spec import materialized_strategy_parameters_hash
from bithumb_bot.paths import PathConfig, PathManager
from bithumb_bot.risk_contract import RiskPolicy


def _runtime() -> dict[str, object]:
    risk_policy = {
        "schema_version": 1,
        "max_daily_loss_krw": 1.0,
        "max_position_loss_pct": 0.0,
        "max_daily_order_count": 1,
        "max_trade_count_per_day": 1,
        "max_drawdown_pct": 0.0,
        "cooldown_after_loss_min": 0,
        "kill_switch": False,
        "max_open_positions": 1,
        "unresolved_order_policy": "block",
        "policy_status": "enabled",
        "missing_policy": "fail_closed_for_live",
        "source": "operation_runtime_settings",
    }
    return {
        "mode": "live",
        "live_dry_run": True,
        "live_real_order_armed": False,
        "strategy_name": "sma_with_filter",
        "strategy_version": "v1",
        "strategy_spec_hash": "sha256:spec",
        "strategy_plugin_contract_hash": "sha256:plugin",
        "market": "KRW-BTC",
        "interval": "1m",
        "strategy_parameters": {"SMA_SHORT": 7},
        "strategy_parameters_hash": materialized_strategy_parameters_hash({"SMA_SHORT": 7}),
        "exit_policy_hash": "sha256:exit",
        "risk_policy": risk_policy,
        "risk_policy_hash": RiskPolicy(**risk_policy).policy_hash(),
        "execution_contract_hash": "sha256:execution",
        "max_order_krw": 50_000.0,
    }


def _approval(runtime: dict[str, object] | None = None, **overrides: object) -> dict[str, object]:
    return build_operation_approval(
        runtime=runtime or _runtime(),
        approved_by=str(overrides.pop("approved_by", "operator")),
        expires_at=str(
            overrides.pop("expires_at", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        ),
        allowed_modes=list(overrides.pop("allowed_modes", ["live_dry_run"])),
        max_order_krw=overrides.pop("max_order_krw", None),
        approved_at=str(overrides.pop("approved_at", "2026-01-01T00:00:00+00:00")),
    )


def _manager(tmp_path: Path) -> PathManager:
    return PathManager(
        project_root=Path(__file__).resolve().parents[1],
        config=PathConfig(
            mode="paper",
            env_root=tmp_path / "env",
            run_root=tmp_path / "run",
            data_root=tmp_path / "data",
            log_root=tmp_path / "logs",
            backup_root=tmp_path / "backup",
            archive_root=tmp_path / "archive",
        ),
    )


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["content_hash"] = compute_operation_approval_hash(payload)
    return payload


def test_operation_approval_missing_fails_closed() -> None:
    result = verify_operation_approval_against_runtime(
        approval_path=None,
        runtime=_runtime(),
        require_approval=True,
    )

    assert result.ok is False
    assert result.reason == "operation_approval_missing"


def test_operation_approval_rejects_repository_local_path() -> None:
    repo_local = Path(__file__).resolve().parents[1] / "operation-approval.json"

    with pytest.raises(OperationApprovalError, match="operation_approval_path_repo_local_not_allowed"):
        write_operation_approval_atomic(repo_local, _approval(), manager=_manager(repo_local.parent))


def test_operation_approval_external_absolute_path_writes_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bithumb_bot.storage_io as storage_io

    destination = tmp_path / "operator-custody" / "approval.json"
    original_replace = storage_io.os.replace
    replace_calls: list[tuple[Path, Path]] = []

    def _recording_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
        replace_calls.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(storage_io.os, "replace", _recording_replace)

    written = write_operation_approval_atomic(destination, _approval(), manager=_manager(tmp_path))

    assert written == destination.resolve()
    assert json.loads(destination.read_text(encoding="utf-8"))["content_hash"].startswith("sha256:")
    assert len(replace_calls) == 1
    assert replace_calls[0][1] == destination
    assert replace_calls[0][0].parent == destination.parent
    assert not replace_calls[0][0].exists()


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (lambda payload: payload.__setitem__("schema_version", 999), "schema_version_unsupported"),
        (lambda payload: payload.__setitem__("approved_by", "tampered"), "content_hash_mismatch"),
        (
            lambda payload: (
                payload.__setitem__("strategy_parameters", {"SMA_SHORT": 8}),
                _rehash(payload),
            ),
            "strategy_parameters_hash_mismatch",
        ),
        (
            lambda payload: (
                payload.__setitem__("risk_policy_hash", "sha256:tampered"),
                _rehash(payload),
            ),
            "risk_policy_hash_mismatch",
        ),
    ],
)
def test_operation_approval_payload_integrity_fails_closed(mutator, error: str) -> None:
    payload = copy.deepcopy(_approval())
    mutator(payload)

    with pytest.raises(OperationApprovalError, match=error):
        validate_operation_approval(payload)


@pytest.mark.parametrize(
    ("field", "actual"),
    [
        ("strategy_name", "different_strategy"),
        ("strategy_version", "v2"),
        ("strategy_spec_hash", "sha256:other-spec"),
        ("strategy_plugin_contract_hash", "sha256:other-plugin"),
        ("market", "KRW-ETH"),
        ("interval", "5m"),
        ("exit_policy_hash", "sha256:other-exit"),
        ("execution_contract_hash", "sha256:other-execution"),
        ("risk_policy_hash", "sha256:other-risk"),
    ],
)
def test_operation_approval_detects_runtime_contract_drift(field: str, actual: object) -> None:
    runtime = _runtime()
    approval = _approval(runtime)
    drifted = {**runtime, field: actual}

    fields = {item["field"] for item in diff_operation_approval_to_runtime(approval, drifted)}

    assert field in fields


def test_operation_approval_detects_strategy_parameter_hash_and_value_drift() -> None:
    runtime = _runtime()
    approval = _approval(runtime)
    parameters = {"SMA_SHORT": 8}
    drifted = {
        **runtime,
        "strategy_parameters": parameters,
        "strategy_parameters_hash": materialized_strategy_parameters_hash(parameters),
    }

    fields = {item["field"] for item in diff_operation_approval_to_runtime(approval, drifted)}

    assert {"strategy_parameters_hash", "strategy_parameters.SMA_SHORT"} <= fields


def test_operation_approval_rejects_disallowed_mode_max_order_and_expiry() -> None:
    runtime = _runtime()
    approval = _approval(runtime, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    drifted = {
        **runtime,
        "live_dry_run": False,
        "live_real_order_armed": True,
        "max_order_krw": 50_001.0,
    }

    fields = {item["field"] for item in diff_operation_approval_to_runtime(approval, drifted)}

    assert {"allowed_mode", "max_order_krw", "expires_at"} <= fields


def test_operation_approval_create_inspect_diff_and_verify_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import bithumb_bot.config as config
    import bithumb_bot.operation_approval as operation_approval

    runtime = _runtime()
    manager = _manager(tmp_path)
    cfg = replace(config.settings, MODE="paper")
    context = AppContext(settings=cfg, path_manager=manager)
    path = tmp_path / "operator-custody" / "approval.json"
    monkeypatch.setattr(config, "PATH_MANAGER", manager)
    monkeypatch.setattr(config, "settings", cfg)
    monkeypatch.setattr(operation_approval, "runtime_contract_from_settings", lambda _settings: runtime)

    assert cli_main(
        [
            "operation-approval-create",
            "--out",
            str(path),
            "--approved-by",
            "operator",
            "--expires-at",
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "--allowed-mode",
            "live_dry_run",
        ],
        context=context,
    ) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert cli_main(["operation-approval-inspect", "--approval", str(path)], context=context) == 0
    assert json.loads(capsys.readouterr().out)["approval"]["content_hash"].startswith("sha256:")

    assert cli_main(["operation-approval-diff", "--approval", str(path)], context=context) == 0
    assert json.loads(capsys.readouterr().out)["mismatches"] == []

    assert cli_main(["operation-approval-verify", "--approval", str(path)], context=context) == 0
    assert json.loads(capsys.readouterr().out)["operation_approval_verification_ok"] is True
