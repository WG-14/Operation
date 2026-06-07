from __future__ import annotations

import pytest

from bithumb_bot.research import process_runtime
from bithumb_bot.research.process_runtime import (
    ResearchProcessRuntimeError,
    resolve_research_process_runtime,
)


@pytest.fixture(autouse=True)
def _clear_process_runtime_env(monkeypatch) -> None:
    for name in (
        "BITHUMB_RESEARCH_ALLOW_UNSAFE_FORK",
        "BITHUMB_RESEARCH_MAX_WORKERS",
        "BITHUMB_RESEARCH_MP_START_METHOD",
        "BITHUMB_TOTAL_PROCESS_BUDGET",
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_WORKER_COUNT",
        "PYTEST_XDIST_WORKERS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_auto_safe_prefers_forkserver_when_available(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["fork", "spawn", "forkserver"])

    runtime = resolve_research_process_runtime(requested_max_workers=2)

    assert runtime.requested_start_method == "auto_safe"
    assert runtime.effective_start_method == "forkserver"
    assert runtime.max_workers_effective == 2


def test_auto_safe_falls_back_to_spawn_without_forkserver(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["spawn", "fork"])

    runtime = resolve_research_process_runtime(requested_max_workers=2)

    assert runtime.effective_start_method == "spawn"


def test_auto_uses_same_safe_preference_order(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["spawn", "forkserver", "fork"])

    runtime = resolve_research_process_runtime(requested_start_method="auto", requested_max_workers=2)

    assert runtime.requested_start_method == "auto"
    assert runtime.effective_start_method == "forkserver"


def test_invalid_method_fails_with_available_methods(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["spawn"])

    with pytest.raises(ResearchProcessRuntimeError, match="auto_safe, auto, forkserver, spawn, fork"):
        resolve_research_process_runtime(requested_start_method="bogus", requested_max_workers=2)


def test_explicit_fork_requires_diagnostic_override(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["fork", "spawn"])
    monkeypatch.setattr(process_runtime, "_parent_thread_count", lambda: 2)

    with pytest.raises(ResearchProcessRuntimeError, match="requires BITHUMB_RESEARCH_ALLOW_UNSAFE_FORK=1"):
        resolve_research_process_runtime(requested_start_method="fork", requested_max_workers=2)

    runtime = resolve_research_process_runtime(
        requested_start_method="fork",
        requested_max_workers=2,
        allow_unsafe_fork=True,
    )

    assert runtime.effective_start_method == "fork"
    assert runtime.unsafe_fork_allowed is True
    assert runtime.parent_thread_count == 2


def test_explicit_fork_allows_environment_diagnostic_override(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["fork", "spawn"])
    monkeypatch.setenv("BITHUMB_RESEARCH_ALLOW_UNSAFE_FORK", "1")

    runtime = resolve_research_process_runtime(requested_start_method="fork", requested_max_workers=2)

    assert runtime.effective_start_method == "fork"
    assert runtime.unsafe_fork_allowed is True


def test_environment_start_method_override(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("BITHUMB_RESEARCH_MP_START_METHOD", "spawn")

    runtime = resolve_research_process_runtime(requested_start_method="forkserver", requested_max_workers=2)

    assert runtime.requested_start_method == "spawn"
    assert runtime.effective_start_method == "spawn"


def test_research_max_workers_env_caps_effective_workers(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("BITHUMB_RESEARCH_MAX_WORKERS", "2")

    runtime = resolve_research_process_runtime(requested_max_workers=4)

    assert runtime.max_workers_requested == 4
    assert runtime.max_workers_effective == 2
    assert runtime.process_budget["research_max_workers_env_cap"] == 2


def test_pytest_xdist_outer_context_detection(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "3")

    runtime = resolve_research_process_runtime(requested_max_workers=2)

    assert runtime.outer_parallel_context == "pytest-xdist"
    assert runtime.process_budget["outer_worker_id"] == "gw1"
    assert runtime.process_budget["outer_worker_count"] == 3


def test_pytest_xdist_outer_count_falls_back_to_pytest_xdist_workers(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKERS", "4")

    runtime = resolve_research_process_runtime(requested_max_workers=2)

    assert runtime.outer_parallel_context == "pytest-xdist"
    assert runtime.process_budget["outer_worker_count"] == 4


def test_total_process_budget_caps_inner_workers_when_outer_count_known(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "2")
    monkeypatch.delenv("BITHUMB_RESEARCH_MAX_WORKERS", raising=False)
    monkeypatch.setenv("BITHUMB_TOTAL_PROCESS_BUDGET", "6")

    runtime = resolve_research_process_runtime(requested_max_workers=8)

    assert runtime.max_workers_effective == 3
    assert runtime.process_budget["total_process_budget"] == 6


def test_total_budget_equal_to_outer_worker_count_caps_inner_worker_to_one(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "8")
    monkeypatch.setenv("BITHUMB_TOTAL_PROCESS_BUDGET", "8")

    runtime = resolve_research_process_runtime(requested_max_workers=2)

    assert runtime.max_workers_requested == 2
    assert runtime.max_workers_effective == 1
    assert runtime.process_budget["outer_worker_count"] == 8
    assert runtime.process_budget["total_process_budget"] == 8
    assert runtime.process_budget["research_max_workers_effective"] == 1


def test_total_budget_matrix_records_effective_worker_metadata(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "8")
    monkeypatch.setenv("BITHUMB_TOTAL_PROCESS_BUDGET", "16")

    runtime = resolve_research_process_runtime(requested_max_workers=4)
    payload = runtime.observability_payload()

    assert runtime.max_workers_effective == 2
    assert payload["research_max_workers_effective"] == 2
    assert payload["process_budget"]["research_max_workers_effective"] == 2
    assert payload["process_budget"]["research_max_workers_requested"] == 4
    assert payload["process_budget"]["outer_parallel_context"] == "pytest-xdist"


@pytest.mark.parametrize(
    ("outer_workers", "total_budget", "requested_workers", "expected_effective"),
    [
        (8, 8, 2, 1),
        (8, 16, 4, 2),
        (4, 16, 8, 4),
        (2, 16, 8, 8),
    ],
)
def test_total_process_budget_matrix_for_xdist_outer_counts(
    monkeypatch,
    outer_workers: int,
    total_budget: int,
    requested_workers: int,
    expected_effective: int,
) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", str(outer_workers))
    monkeypatch.setenv("BITHUMB_TOTAL_PROCESS_BUDGET", str(total_budget))

    runtime = resolve_research_process_runtime(requested_max_workers=requested_workers)

    assert runtime.max_workers_effective == expected_effective
    assert runtime.process_budget["research_max_workers_effective"] == expected_effective
    assert runtime.process_budget["outer_worker_count"] == outer_workers
    assert runtime.process_budget["total_process_budget"] == total_budget


def test_total_process_budget_caps_inner_workers_without_outer_count(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKERS", raising=False)
    monkeypatch.delenv("BITHUMB_RESEARCH_MAX_WORKERS", raising=False)
    monkeypatch.setenv("BITHUMB_TOTAL_PROCESS_BUDGET", "3")

    runtime = resolve_research_process_runtime(requested_max_workers=8)

    assert runtime.max_workers_effective == 3
    assert runtime.process_budget["outer_worker_count"] is None


def test_unavailable_start_method_fallback_is_observable(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])

    runtime = resolve_research_process_runtime(
        requested_start_method="auto_safe",
        requested_max_workers=2,
        unavailable_start_methods=("forkserver",),
        fallback_reason="forkserver_pool_create_permission_error",
    )

    assert runtime.effective_start_method == "spawn"
    assert runtime.process_start_method_fallback == {
        "schema_version": 1,
        "requested_process_start_method": "auto_safe",
        "unavailable_process_start_methods": ["forkserver"],
        "effective_process_start_method": "spawn",
        "reason": "forkserver_pool_create_permission_error",
    }
    assert runtime.observability_payload()["process_start_method_fallback"] == runtime.process_start_method_fallback


def test_process_policy_observability_records_effective_budget(monkeypatch) -> None:
    monkeypatch.setattr(process_runtime.mp, "get_all_start_methods", lambda: ["forkserver", "spawn"])
    monkeypatch.setenv("BITHUMB_RESEARCH_MAX_WORKERS", "2")

    payload = process_runtime.process_policy_observability(
        requested_start_method="auto_safe",
        requested_max_workers=5,
    )

    assert payload["research_max_workers_requested"] == 5
    assert payload["research_max_workers_effective"] == 2
    assert payload["process_budget"]["research_max_workers_effective"] == 2
