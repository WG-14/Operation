from __future__ import annotations

import argparse
import json
from pathlib import Path

from bithumb_bot.cli.registry import CommandSpec

from ._helpers import make_spec


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _create(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.config import PATH_MANAGER, settings
    from bithumb_bot.operation_approval import (
        OperationApprovalError,
        build_operation_approval,
        runtime_contract_from_settings,
        write_operation_approval_atomic,
    )

    try:
        runtime = runtime_contract_from_settings(settings)
        approval = build_operation_approval(
            runtime=runtime,
            approved_by=str(args.approved_by),
            expires_at=str(args.expires_at),
            allowed_modes=list(args.allowed_mode),
            max_order_krw=args.max_order_krw,
        )
        path = write_operation_approval_atomic(Path(args.out), approval, manager=PATH_MANAGER)
    except (OperationApprovalError, OSError, ValueError) as exc:
        _print({"ok": False, "command": "operation-approval-create", "error": str(exc)})
        return 1
    _print({
        "ok": True,
        "command": "operation-approval-create",
        "operation_approval_path": str(path),
        "operation_approval_hash": approval["content_hash"],
        "allowed_modes": approval["allowed_modes"],
    })
    return 0


def _inspect(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.operation_approval import OperationApprovalError, load_operation_approval

    try:
        approval = load_operation_approval(args.approval)
    except OperationApprovalError as exc:
        _print({"ok": False, "command": "operation-approval-inspect", "error": str(exc)})
        return 1
    _print({"ok": True, "command": "operation-approval-inspect", "approval": approval})
    return 0


def _diff(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.config import settings
    from bithumb_bot.operation_approval import (
        OperationApprovalError,
        diff_operation_approval_to_runtime,
        load_operation_approval,
        runtime_contract_from_settings,
    )

    try:
        approval = load_operation_approval(args.approval)
        mismatches = diff_operation_approval_to_runtime(approval, runtime_contract_from_settings(settings))
    except OperationApprovalError as exc:
        _print({"ok": False, "command": "operation-approval-diff", "error": str(exc)})
        return 1
    _print({"ok": not mismatches, "command": "operation-approval-diff", "mismatches": list(mismatches)})
    return 0 if not mismatches else 1


def _verify(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.config import settings
    from bithumb_bot.operation_approval import runtime_contract_from_settings, verify_operation_approval_against_runtime

    result = verify_operation_approval_against_runtime(
        approval_path=args.approval,
        runtime=runtime_contract_from_settings(settings),
        require_approval=True,
    )
    _print({"command": "operation-approval-verify", **result.audit_fields()})
    return 0 if result.ok else 1


def command_specs() -> list[CommandSpec]:
    return [
        make_spec("operation-approval-create", domain="operation_approval", handler=_create, help="create a reviewed Operation approval from the current Operation runtime contract", description="Writes an operator-custodied approval artifact; it does not consume promotion, manifest, dataset, lineage, or experiment artifacts.", build=_build_create, produces_artifact=True, json_output_supported=True),
        make_spec("operation-approval-inspect", domain="operation_approval", handler=_inspect, help="inspect and hash-validate an Operation approval", build=_build_approval, json_output_supported=True),
        make_spec("operation-approval-diff", domain="operation_approval", handler=_diff, help="fail-closed compare an Operation approval with current runtime", build=_build_approval, json_output_supported=True),
        make_spec("operation-approval-verify", domain="operation_approval", handler=_verify, help="verify an Operation approval against current runtime", build=_build_approval, json_output_supported=True),
    ]


def _build_approval(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--approval", required=True)


def _build_create(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--expires-at", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--allowed-mode", action="append", required=True, choices=("paper", "live_dry_run", "small_live"))
    parser.add_argument("--max-order-krw", type=float)
