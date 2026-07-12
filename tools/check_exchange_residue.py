#!/usr/bin/env python3
"""Fail CI if the retired exchange identity reappears in tracked files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


FORBIDDEN = ("bit" + "humb", "bit" + "humb_bot", "bit" + "humb-bot", "api." + "bithumb.com", "BIT" + "HUMB_")


def main() -> int:
    files = subprocess.run(["git", "ls-files"], check=True, text=True, capture_output=True).stdout.splitlines()
    matches = [path for path in files if Path(path).is_file() and Path(path).suffix not in {".png", ".jpg"} and any(token.lower() in Path(path).read_text(encoding="utf-8", errors="ignore").lower() for token in FORBIDDEN)]
    if matches:
        print("retired exchange residue remains:", *matches, sep="\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
