from __future__ import annotations

from pathlib import Path


FORBIDDEN_TOKENS = (
    "h" + "74",
    "daily_" + "participation_sma",
    "fixed_fill_qty_" + "until_exit",
    "experiment_execution_" + "contract",
    "live_observation_" + "authority",
)

SCANNED_PATHS = ("src", "tests", "tools", "docs", ".env.example")
EXCLUDED_SUFFIXES = {".pyc", ".sqlite", ".sqlite3", ".db"}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _iter_scannable_files(repository_root: Path):
    for relative_path in SCANNED_PATHS:
        path = repository_root / relative_path
        if path.is_file():
            yield path
            continue
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() not in EXCLUDED_SUFFIXES:
                yield candidate


def test_retired_strategy_identifiers_are_absent() -> None:
    repository_root = _repository_root()
    findings: list[str] = []

    for path in _iter_scannable_files(repository_root):
        content = path.read_bytes()
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="replace").casefold()
        for token in FORBIDDEN_TOKENS:
            if token.casefold() in text:
                findings.append(f"{path.relative_to(repository_root)}: {token}")

    assert not findings, "Retired strategy identifiers found:\n" + "\n".join(findings)
