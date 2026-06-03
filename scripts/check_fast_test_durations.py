#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


DURATION_LINE_RE = re.compile(r"^\s*(?P<seconds>\d+(?:\.\d+)?)s\s+(?P<phase>\w+)\s+(?P<nodeid>\S.*::\S+)\s*$")


@dataclass(frozen=True)
class TestDuration:
    seconds: float
    phase: str
    nodeid: str


def parse_pytest_durations(text: str) -> list[TestDuration]:
    durations: list[TestDuration] = []
    for line in text.splitlines():
        match = DURATION_LINE_RE.match(line)
        if match is None:
            continue
        durations.append(
            TestDuration(
                seconds=float(match.group("seconds")),
                phase=match.group("phase"),
                nodeid=match.group("nodeid"),
            )
        )
    return durations


def violations_over_budget(durations: list[TestDuration], max_seconds: float) -> list[TestDuration]:
    return sorted(
        (duration for duration in durations if duration.seconds > max_seconds),
        key=lambda duration: (-duration.seconds, duration.nodeid, duration.phase),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when default-fast pytest durations exceed budget.")
    parser.add_argument("duration_log", type=Path)
    parser.add_argument("--max-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)

    durations = parse_pytest_durations(args.duration_log.read_text(encoding="utf-8"))
    violations = violations_over_budget(durations, args.max_seconds)
    if violations:
        print(
            f"default-fast duration budget exceeded: max_seconds={args.max_seconds:g}",
            file=sys.stderr,
        )
        for violation in violations:
            print(
                f"- {violation.seconds:.2f}s {violation.phase} {violation.nodeid}",
                file=sys.stderr,
            )
        return 1
    print(f"default-fast duration guard: ok ({len(durations)} reported durations, max_seconds={args.max_seconds:g})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
