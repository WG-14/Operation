from __future__ import annotations

import argparse

from bithumb_bot.cli.registry import CommandSpec

from ._helpers import make_spec


def _runtime_replay(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.operation_approval import load_operation_approval

    approval = load_operation_approval(str(args.approval))
    print(
        "runtime replay requires the Operation replay service; "
        f"approval_hash={approval['content_hash']}"
    )
    return 1


def _replay_decision(args: argparse.Namespace, _context) -> int:
    print(
        "single-decision replay is unavailable until an Operation replay service is configured; "
        f"strategy={args.strategy} candle_ts={args.candle_ts}"
    )
    return 1


def command_specs() -> list[CommandSpec]:
    common = dict(read_only=True, produces_artifact=True, json_output_supported=True)
    return [
        make_spec(
            "runtime-replay-decisions",
            domain="runtime",
            handler=_runtime_replay,
            help="validate the Operation approval selected for runtime replay",
            description="Read-only Operation replay boundary; does not call live broker APIs or submit orders.",
            build=_build_runtime_replay,
            **common,
        ),
        make_spec(
            "replay-decision",
            domain="runtime",
            handler=_replay_decision,
            help="inspect the Operation single-decision replay boundary",
            description="Read-only Operation decision diagnostic; does not call live broker APIs or submit orders.",
            build=_build_replay_decision,
            **common,
        ),
    ]


def _build_runtime_replay(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--approval", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--through-ts-list", required=True)
    parser.add_argument("--out", required=True)


def _build_replay_decision(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--candle-ts", required=True, type=int)
    parser.add_argument("--readiness-json")
    parser.add_argument("--json", action="store_true")
