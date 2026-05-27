from __future__ import annotations

from bithumb_bot.cli.registry import CommandSpec

from ._legacy import legacy_specs


def command_specs() -> list[CommandSpec]:
    return legacy_specs(
        [
            "profile-generate",
            "profile-diff",
            "profile-verify",
            "profile-promote",
        ],
        domain="profile",
        read_only=True,
        produces_artifact=True,
        json_output_supported=True,
    )
