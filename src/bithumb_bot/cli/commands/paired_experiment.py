from __future__ import annotations

import argparse
import json

from bithumb_bot.cli.registry import CommandSpec

from ._helpers import make_spec


def _paired_experiment(args: argparse.Namespace, context) -> int:
    from bithumb_bot.config import settings
    from bithumb_bot.db_core import ensure_db
    from bithumb_bot.paired_experiment import run_closed_candle_paired_experiment

    now_ms = int(float(context.clock()) * 1000)
    payload = run_closed_candle_paired_experiment(
        db_factory=lambda: ensure_db(ensure_schema_ready=False),
        run_id=str(args.run_id),
        market=str(args.market or settings.PAIR),
        interval=str(args.interval or settings.INTERVAL),
        now_ms=now_ms,
        profile_hash=str(args.profile_hash),
        strategy_parameters_hash=str(args.strategy_parameters_hash),
        submit_enabled=bool(args.submit_enabled),
        broker_submit=None,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


def _build(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--market")
    parser.add_argument("--interval")
    parser.add_argument("--profile-hash", required=True)
    parser.add_argument("--strategy-parameters-hash", required=True)
    parser.add_argument("--submit-enabled", action="store_true")


def command_specs() -> list[CommandSpec]:
    return [
        make_spec(
            "paired-experiment",
            domain="research",
            handler=_paired_experiment,
            help="compare shadow backtest and operational lanes on one closed candle",
            description="Run a paired diagnostic comparison for one closed runtime candle.",
            build=_build,
            read_only=True,
            produces_artifact=True,
            json_output_supported=True,
        )
    ]
