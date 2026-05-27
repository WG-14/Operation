from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "report",
            "ops-report",
            "risk-report",
            "fee-diagnostics",
            "strategy-report",
            "experiment-report",
            "cash-drift-report",
            "decision-telemetry",
            "decision-attribution",
            "execution-quality-report",
        ],
        domain="reports",
        read_only=True,
        produces_artifact=True,
        json_output_supported=True,
    )
