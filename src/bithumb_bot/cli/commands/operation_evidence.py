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


def _h74_observation_authority_generate(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.h74_observation import cmd_h74_observation_authority_generate

    return int(cmd_h74_observation_authority_generate(out_path=str(args.out) if args.out else None))


def _h74_observation_authority_verify(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.h74_observation import cmd_h74_observation_authority_verify

    return int(cmd_h74_observation_authority_verify(authority_path=str(args.authority)))


def _h74_source_observation_authority_generate(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.h74_observation import cmd_h74_source_observation_authority_generate

    return int(
        cmd_h74_source_observation_authority_generate(
            out_path=str(args.out) if args.out else None,
            source_candidate_artifact_hash=str(args.source_candidate_artifact_hash),
            backtest_report_hash=args.backtest_report_hash,
            validation_run_hash=args.validation_run_hash,
            code_commit_sha=args.code_commit_sha,
            experiment_envelope_path=args.experiment_envelope,
        )
    )


def _h74_source_observation_authority_verify(args: argparse.Namespace, _context) -> int:
    from bithumb_bot.h74_observation import cmd_h74_source_observation_authority_verify

    return int(cmd_h74_source_observation_authority_verify(authority_path=str(args.authority)))


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
        make_spec(
            "h74-observation-authority-generate",
            domain="operation_evidence",
            handler=_h74_observation_authority_generate,
            help="generate h74 50k live-observation authority",
            description="Generate non-promotion h74 live-observation authority for the 50,000 KRW capital-scaled variant. This artifact is extra validation/reporting only and is not an approved-profile substitute.",
            build=lambda p: p.add_argument("--out"),
            **common,
        ),
        make_spec(
            "h74-observation-authority-verify",
            domain="operation_evidence",
            handler=_h74_observation_authority_verify,
            help="verify h74 50k live-observation authority",
            description="Verify a non-promotion h74 live-observation authority artifact. This artifact is not an approved-profile substitute.",
            build=lambda p: p.add_argument("--authority", required=True),
            **common,
        ),
        make_spec(
            "h74-source-observation-authority-generate",
            domain="operation_evidence",
            handler=_h74_source_observation_authority_generate,
            help="generate h74 source-candidate live-observation authority",
            description="Generate non-production h74 source-candidate observation authority for 100,000 KRW time-boxed live forward observation. This is not an approved-profile substitute.",
            build=_build_h74_source_observation_authority_generate,
            **common,
        ),
        make_spec(
            "h74-source-observation-authority-verify",
            domain="operation_evidence",
            handler=_h74_source_observation_authority_verify,
            help="verify h74 source-candidate observation authority.",
            description="Verify non-production h74 source-candidate observation authority.",
            build=lambda p: p.add_argument("--authority", required=True),
            **common,
        ),
    ]


def _build_h74_source_observation_authority_generate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out")
    parser.add_argument("--source-candidate-artifact-hash", required=True)
    parser.add_argument("--backtest-report-hash")
    parser.add_argument("--validation-run-hash")
    parser.add_argument("--code-commit-sha")
    parser.add_argument("--experiment-envelope", required=True)


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
