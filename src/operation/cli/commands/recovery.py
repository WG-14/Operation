from __future__ import annotations

import argparse

from operation.cli.registry import CommandSpec

from ._helpers import make_spec


def _json_handler(function_name: str):
    def _handler(args: argparse.Namespace, _context) -> int | None:
        from operation import operator_commands

        return getattr(operator_commands, function_name)(as_json=bool(args.json))

    return _handler


def _simple(function_name: str):
    def _handler(_args: argparse.Namespace, _context) -> int | None:
        from operation import operator_commands

        return getattr(operator_commands, function_name)()

    return _handler


def _diagnose_fill_trade_linkage(args: argparse.Namespace, _context) -> None:
    from operation.operator_commands import cmd_diagnose_fill_trade_linkage

    cmd_diagnose_fill_trade_linkage(as_json=bool(args.json), apply_safe=bool(args.apply_safe))


def command_specs() -> list[CommandSpec]:
    return [
        make_spec(
            "recovery-report",
            domain="recovery",
            handler=_json_handler("cmd_recovery_report"),
            help="show unresolved/recovery-required order report",
            description="Show unresolved/recovery-required orders and resume blockers.",
            build=lambda p: p.add_argument("--json", action="store_true"),
            json_output_supported=True,
        ),
        make_spec(
            "repair-plan",
            domain="recovery",
            handler=_json_handler("cmd_repair_plan"),
            help="show non-mutating accounting recovery plan preview",
            description=(
                "Aggregate existing recovery and repair previews into one operator-oriented, "
                "read-only non-mutating plan."
            ),
            build=lambda p: p.add_argument("--json", action="store_true"),
            json_output_supported=True,
        ),
        make_spec(
            "restart-checklist",
            domain="recovery",
            handler=_simple("cmd_restart_checklist"),
            help="print restart safety checklist before resume",
            description="Print restart safety checklist for operator restart verification.",
        ),
        make_spec(
            "residual-closeout-plan",
            domain="recovery",
            handler=_json_handler("cmd_residual_closeout_plan"),
            help="show read-only residual-only closeout and policy review plan",
            description=(
                "Summarize converged non-executable residual holdings without mutating the DB or "
                "recommending position-authority rebuild."
            ),
            build=lambda p: p.add_argument("--json", action="store_true"),
            json_output_supported=True,
        ),
        make_spec(
            "diagnose-fill-trade-linkage",
            domain="recovery",
            handler=_diagnose_fill_trade_linkage,
            help="summarize fills that are missing trade_id linkage",
            description="Diagnose fills.trade_id gaps and optionally repair exactly safe matches.",
            build=_build_fill_trade_linkage,
            read_only=False,
            mutating=True,
            writes_db=True,
            json_output_supported=True,
        ),
    ]


def _build_fill_trade_linkage(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--apply-safe",
        action="store_true",
        help="update only rows with exactly one safe candidate trade; ambiguous and unmatchable rows are skipped",
    )

