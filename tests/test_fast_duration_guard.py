from __future__ import annotations

from scripts.check_fast_test_durations import parse_pytest_durations, violations_over_budget


def test_parse_pytest_durations_extracts_reported_test_phases() -> None:
    text = """
============================= slowest 3 durations =============================
12.34s call tests/test_slow.py::test_default_fast_regression
0.42s setup tests/test_setup.py::test_fixture_cost
not a duration line
0.25s teardown tests/test_teardown.py::test_cleanup
"""

    durations = parse_pytest_durations(text)

    assert [duration.nodeid for duration in durations] == [
        "tests/test_slow.py::test_default_fast_regression",
        "tests/test_setup.py::test_fixture_cost",
        "tests/test_teardown.py::test_cleanup",
    ]
    assert [duration.phase for duration in durations] == ["call", "setup", "teardown"]
    assert [duration.seconds for duration in durations] == [12.34, 0.42, 0.25]


def test_duration_guard_flags_only_tests_above_budget() -> None:
    durations = parse_pytest_durations(
        """
9.99s call tests/test_ok.py::test_under_budget
10.01s call tests/test_slow.py::test_over_budget
15.00s setup tests/test_slowest.py::test_fixture_over_budget
"""
    )

    violations = violations_over_budget(durations, max_seconds=10.0)

    assert [violation.nodeid for violation in violations] == [
        "tests/test_slowest.py::test_fixture_over_budget",
        "tests/test_slow.py::test_over_budget",
    ]
