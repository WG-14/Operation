#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
import os


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    sys.path.insert(0, str(repo_root))
    from tests.policy.research_runner_policy import discover_policy_violations

    violations = discover_policy_violations(repo_root / "tests")
    if violations:
        print("research test policy violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("research test policy: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
