from __future__ import annotations

import ast
from pathlib import Path

import pytest

import bithumb_bot.artifact_hashing as operation_hashing


_CANONICAL_PAYLOAD = {
    "z": 7,
    "korean": "한글",
    "nested": {"tuple": ("값", 2), "alpha": True},
    "a": None,
}
_CANONICAL_JSON_GOLDEN = (
    b'{"a":null,"korean":"\xed\x95\x9c\xea\xb8\x80",'
    b'"nested":{"alpha":true,"tuple":["\xea\xb0\x92",2]},"z":7}'
)
_CANONICAL_SHA256_GOLDEN = "841b8702f50e71eb1801d47c4d6c655780b8cfff1bc2514b35b768ac053b9524"

_CONTENT_PAYLOAD = {
    "created_at": "2026-07-11T00:00:00Z",
    "keep": {"korean": "한글", "values": [1, 2]},
    "generated_at": "2026-07-11T00:00:01Z",
}
_CONTENT_PAYLOAD_GOLDEN = {"keep": {"korean": "한글", "values": [1, 2]}}

_REPORT_PAYLOAD = {
    "content_hash": "sha256:ignored",
    "generated_at": "2026-07-11T00:00:01Z",
    "artifact_paths": {"report": "/runtime/report.json"},
    "keep": {
        "tuple": ("한글", 2),
        "nested": {
            "wall_seconds": 0.3,
            "value": "retained",
            "run_environment": {"host": "runtime-only"},
        },
    },
    "items": [{"rss_mb": 12.5, "name": "kept"}],
}
_REPORT_PAYLOAD_GOLDEN = {
    "keep": {"tuple": ["한글", 2], "nested": {"value": "retained"}},
    "items": [{"name": "kept"}],
}
_REPORT_SHA256_GOLDEN = "b601182e2fbdbf3c1821e02ab07aedd7d9ee1603e14d356cbbf89609653cb52a"


def test_canonical_json_bytes_uses_fixed_research_compatible_golden_vector() -> None:
    assert operation_hashing.canonical_json_bytes(_CANONICAL_PAYLOAD) == _CANONICAL_JSON_GOLDEN


def test_sha256_helpers_use_fixed_research_compatible_golden_vector() -> None:
    assert operation_hashing.sha256_hex(_CANONICAL_PAYLOAD) == _CANONICAL_SHA256_GOLDEN
    assert operation_hashing.sha256_prefixed(_CANONICAL_PAYLOAD) == f"sha256:{_CANONICAL_SHA256_GOLDEN}"


def test_content_hash_payload_uses_fixed_research_compatible_golden_vector() -> None:
    assert operation_hashing.content_hash_payload(_CONTENT_PAYLOAD) == _CONTENT_PAYLOAD_GOLDEN


def test_report_content_hash_payload_uses_fixed_research_compatible_golden_vector() -> None:
    normalized = operation_hashing.report_content_hash_payload(_REPORT_PAYLOAD)

    assert normalized == _REPORT_PAYLOAD_GOLDEN
    assert operation_hashing.sha256_hex(normalized) == _REPORT_SHA256_GOLDEN


def test_hashing_is_independent_of_dict_key_order() -> None:
    first = {"z": [3, 2, 1], "a": {"y": "value", "b": True}}
    second = {"a": {"b": True, "y": "value"}, "z": [3, 2, 1]}

    assert operation_hashing.canonical_json_bytes(first) == operation_hashing.canonical_json_bytes(second)
    assert operation_hashing.sha256_prefixed(first) == operation_hashing.sha256_prefixed(second)


def test_canonical_json_bytes_preserves_utf8_korean_text() -> None:
    assert operation_hashing.canonical_json_bytes({"message": "한글"}) == b'{"message":"\xed\x95\x9c\xea\xb8\x80"}'


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_hashing_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError):
        operation_hashing.canonical_json_bytes({"value": value})
    with pytest.raises(ValueError):
        operation_hashing.sha256_prefixed({"value": value})


def test_hashing_observability_records_payload_sizes_and_latest_largest_label() -> None:
    smaller = {"payload": [1]}
    larger = {"payload": [1, 2, 3]}

    with operation_hashing.observe_hashing() as observer:
        operation_hashing.sha256_hex(smaller, label="smaller")
        operation_hashing.sha256_prefixed(larger, label="larger")

    assert observer.as_dict() == {
        "hash_call_count": 2,
        "observed_hash_payload_bytes": len(operation_hashing.canonical_json_bytes(smaller))
        + len(operation_hashing.canonical_json_bytes(larger)),
        "largest_hash_payload_bytes": len(operation_hashing.canonical_json_bytes(larger)),
        "largest_hash_label": "larger",
    }


def test_operation_hash_observer_uses_operations_owned_context_name() -> None:
    assert operation_hashing._HASH_OBSERVER.name == "operation_hash_observer"


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
