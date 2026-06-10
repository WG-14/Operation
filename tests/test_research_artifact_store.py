from __future__ import annotations

import json
from pathlib import Path

import pytest

from bithumb_bot.research.artifact_store import ArtifactBudget, ArtifactBudgetExceeded, ArtifactStore


def _json_bytes(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 1


def test_artifact_budget_exceeded_reports_attempted_and_prior_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path, budget=ArtifactBudget(max_artifact_bytes=40))
    first_payload = {"x": "a" * 8}
    second_payload = {"x": "b" * 32}
    path = tmp_path / "candidate_results" / "candidate_001.json"

    first = store.write_json_atomic(path, first_payload)
    with pytest.raises(ArtifactBudgetExceeded) as excinfo:
        store.write_json_atomic(tmp_path / "candidate_results" / "candidate_002.json", second_payload)

    payload = excinfo.value.as_dict()
    attempted = _json_bytes(second_payload)
    assert payload["reason"] == "artifact_budget_max_artifact_bytes_exceeded"
    assert payload["attempted_write_bytes"] == attempted
    assert payload["prior_total_bytes"] == first.bytes
    assert payload["next_total_bytes"] == first.bytes + attempted
    assert payload["observed"] == payload["next_total_bytes"]
    assert payload["limit"] == 40
    assert payload["path"].endswith("candidate_002.json")
    assert payload["overwrite_existing_path"] is False


def test_artifact_budget_exceeded_reports_overwrite_existing_path(tmp_path: Path) -> None:
    first_payload = {"x": "a"}
    second_payload = {"x": "b" * 64}
    first_bytes = _json_bytes(first_payload)
    store = ArtifactStore(root=tmp_path, budget=ArtifactBudget(max_artifact_bytes=first_bytes + 8))
    path = tmp_path / "candidate_results" / "candidate_001.json"

    store.write_json_atomic(path, first_payload)
    with pytest.raises(ArtifactBudgetExceeded) as excinfo:
        store.write_json_atomic(path, second_payload)

    payload = excinfo.value.as_dict()
    assert payload["attempted_write_bytes"] == _json_bytes(second_payload)
    assert payload["prior_total_bytes"] == first_bytes
    assert payload["next_total_bytes"] == first_bytes + _json_bytes(second_payload)
    assert payload["overwrite_existing_path"] is True


def test_total_bytes_counts_repeated_writes_to_same_path(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path, budget=ArtifactBudget(max_artifact_bytes=10_000))
    path = tmp_path / "candidate_results" / "candidate_001.json"
    first_payload = {"candidate": "first"}
    second_payload = {"candidate": "second", "extra": "x" * 20}

    first = store.write_json_atomic(path, first_payload)
    second = store.write_json_atomic(path, second_payload)

    assert store.file_count == 1
    assert store.total_bytes == first.bytes + second.bytes
    assert path.stat().st_size == second.bytes


def test_cumulative_budget_can_exceed_retained_file_size(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path, budget=ArtifactBudget(max_artifact_bytes=10_000))
    path = tmp_path / "candidate_results" / "candidate_001.json"
    payloads = [
        {"candidate": "first", "padding": "x" * 20},
        {"candidate": "second", "padding": "y" * 20},
        {"candidate": "third", "padding": "z" * 20},
    ]

    events = [store.write_json_atomic(path, payload) for payload in payloads]

    assert store.total_bytes == sum(event.bytes for event in events)
    assert path.stat().st_size == events[-1].bytes
    assert store.total_bytes > path.stat().st_size


def test_budget_failure_payload_is_serializable(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path, budget=ArtifactBudget(max_artifact_bytes=1))

    with pytest.raises(ArtifactBudgetExceeded) as excinfo:
        store.write_json_atomic(tmp_path / "candidate.json", {"x": "payload"})

    json.dumps(excinfo.value.as_dict(), sort_keys=True)
