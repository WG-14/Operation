from __future__ import annotations

import ast
from pathlib import Path

import bithumb_bot.artifact_hashing as operation_hashing
import bithumb_bot.research.hashing as research_hashing


def test_operation_artifact_hashing_matches_research_hashing() -> None:
    payload = {
        "created_at": "2026-07-11T00:00:00Z",
        "generated_at": "2026-07-11T00:00:01Z",
        "unicode": "한글",
        "nested": {
            "run_environment": {"host": "runtime-only"},
            "keep": [3, {"wall_seconds": 0.1, "value": True}],
        },
    }

    assert operation_hashing.REPORT_TOP_LEVEL_HASH_EXCLUDED_FIELDS == research_hashing.REPORT_TOP_LEVEL_HASH_EXCLUDED_FIELDS
    assert operation_hashing.REPORT_RUNTIME_ONLY_FIELDS == research_hashing.REPORT_RUNTIME_ONLY_FIELDS
    assert operation_hashing.canonical_json_bytes(payload) == research_hashing.canonical_json_bytes(payload)
    assert operation_hashing.sha256_hex(payload) == research_hashing.sha256_hex(payload)
    assert operation_hashing.sha256_prefixed(payload) == research_hashing.sha256_prefixed(payload)
    assert operation_hashing.content_hash_payload(payload) == research_hashing.content_hash_payload(payload)
    assert operation_hashing.report_content_hash_payload(payload) == research_hashing.report_content_hash_payload(payload)


def test_operation_artifact_hashing_observability_matches_research_hashing() -> None:
    payload = {"payload": [1, 2, 3]}

    with operation_hashing.observe_hashing() as operation_observer:
        operation_hashing.sha256_prefixed(payload, label="payload")
    with research_hashing.observe_hashing() as research_observer:
        research_hashing.sha256_prefixed(payload, label="payload")

    assert operation_observer.as_dict() == research_observer.as_dict()


def test_operation_artifact_hashing_does_not_import_research() -> None:
    source = ast.parse(Path(operation_hashing.__file__).read_text(encoding="utf-8"))
    assert all(
        not (
            isinstance(node, ast.Import)
            and any(alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.") for alias in node.names)
        )
        and not (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "bithumb_bot.research" or node.module.startswith("bithumb_bot.research."))
        )
        for node in ast.walk(source)
    )
