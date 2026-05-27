from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "research-backtest",
            "research-verify-audit",
            "research-validate",
            "research-readiness",
            "research-walk-forward",
            "research-promote-candidate",
            "research-reproduce",
            "research-registry-inspect",
            "research-registry-validate",
            "research-mark-attempt-aborted",
            "research-export-decisions",
            "runtime-replay-decisions",
            "replay-decision",
            "decision-equivalence",
            "candidate-regime-policy-equivalence-evidence",
        ],
        domain="research",
        read_only=True,
        produces_artifact=True,
        json_output_supported=True,
    )
