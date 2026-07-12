#!/usr/bin/env python3
"""Fail CI if the retired exchange identity reappears in tracked files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from collections.abc import Iterable


FORBIDDEN = ("bit" + "humb", "bit" + "humb_bot", "bit" + "humb-bot", "api." + "bit" + "humb.com", "BIT" + "HUMB_")


def find_forbidden_paths(paths: Iterable[str]) -> list[str]:
    return [path for path in paths if any(token.lower() in path.lower() for token in FORBIDDEN)]


def find_forbidden_contents(paths: Iterable[str]) -> list[str]:
    matches: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(token.lower() in content.lower() for token in FORBIDDEN):
            matches.append(str(path))
    return matches


def main() -> int:
    files = subprocess.run(["git", "ls-files"], check=True, text=True, capture_output=True).stdout.splitlines()
    matches = sorted(set(find_forbidden_paths(files) + find_forbidden_contents(files)))
    if matches:
        print("retired exchange residue remains:", *matches, sep="\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
