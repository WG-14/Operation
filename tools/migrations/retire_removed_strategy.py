#!/usr/bin/env python3
"""Offline plan/apply/backup verification for removed strategy retirement."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from _offline_retirement import (
    RetirementApplyError, SafetyCheckError, apply_plan, build_plan, canonical_json, load_plan,
    verify_backup, write_plan,
)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SafetyCheckError("invalid_arguments:" + message)


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(add_help=False)
    subcommands = parser.add_subparsers(dest="command", required=True)
    plan = subcommands.add_parser("plan", add_help=False)
    plan.add_argument("--mode", required=True)
    plan.add_argument("--pair", required=True)
    plan.add_argument("--db", type=Path, required=True)
    plan.add_argument("--backup", type=Path, required=True)
    plan.add_argument("--target-state-action", choices=("retain", "clear"))
    plan.add_argument("--output", type=Path, required=True)
    apply = subcommands.add_parser("apply", add_help=False)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-hash", required=True)
    apply.add_argument("--broker-local-converged", action="store_true")
    apply.add_argument("--confirm", default="")
    backup = subcommands.add_parser("verify-backup", add_help=False)
    backup.add_argument("--plan", type=Path, required=True)
    backup.add_argument("--backup", type=Path, required=True)
    backup.add_argument("--expected-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "plan":
            plan = build_plan(mode=args.mode, pair=args.pair, db_path=args.db, backup_path=args.backup, target_state_action=args.target_state_action)
            write_plan(args.output, plan)
            payload = {
                **json.loads(canonical_json(plan.__dict__)), "database_modified": False,
                "backup_created": False, "transaction_committed": False,
                "commit_outcome": "not_started", "recovery_required": False,
                "recommended_action": "none",
            }
        elif args.command == "apply":
            payload = apply_plan(plan_path=args.plan, expected_plan_hash=args.plan_hash, confirmation=args.confirm, broker_local_converged=args.broker_local_converged)
        else:
            payload = verify_backup(plan=load_plan(args.plan), backup_path=args.backup, expected_sha256=args.expected_sha256)
    except RetirementApplyError as exc:
        print(canonical_json(exc.as_payload()))
        return exc.exit_code
    except (SafetyCheckError, sqlite3.Error, OSError) as exc:
        code, _, details = str(exc).partition(":")
        payload = {
            "status": "refused", "reason_code": code, "phase": "preflight",
            "database_modified": False, "backup_created": False, "backup_verified": False,
            "transaction_started": False, "transaction_committed": False,
            "commit_outcome": "not_started", "rollback_attempted": False,
            "rollback_succeeded": None, "foreign_keys_reenabled": None,
            "post_commit_verified": False, "recovery_required": False,
            "recommended_action": "correct the refusal reason; create a new plan before retry",
        }
        if code == "retirement_plan_stale" and details:
            payload["changed_fields"] = sorted(item for item in details.split(",") if item)
        print(canonical_json(payload))
        return 2
    print(canonical_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
