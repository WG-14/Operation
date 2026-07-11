from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bithumb_bot import notification_diagnostics
from bithumb_bot.notification_policy import resolve_operation_notification_policy


def test_explicit_operation_policy_takes_priority_over_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPERATION_NOTIFICATION_POLICY", "disabled")
    monkeypatch.setenv("RESEARCH_NOTIFICATION_POLICY", "best_effort")

    assert resolve_operation_notification_policy("require_delivery") == "require_delivery"


def test_operation_notification_policy_environment_is_used(monkeypatch) -> None:
    monkeypatch.setenv("OPERATION_NOTIFICATION_POLICY", "require_delivery")
    monkeypatch.setenv("RESEARCH_NOTIFICATION_POLICY", "disabled")

    assert resolve_operation_notification_policy() == "require_delivery"


def test_operation_notification_policy_defaults_to_best_effort(monkeypatch) -> None:
    monkeypatch.delenv("OPERATION_NOTIFICATION_POLICY", raising=False)
    monkeypatch.delenv("RESEARCH_NOTIFICATION_POLICY", raising=False)

    assert resolve_operation_notification_policy() == "best_effort"


@pytest.mark.parametrize("policy", ["best_effort", "require_delivery", "disabled"])
def test_operation_notification_policy_allows_supported_values(policy: str) -> None:
    assert resolve_operation_notification_policy(policy) == policy


def test_operation_notification_policy_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="invalid operation notification policy"):
        resolve_operation_notification_policy("deliver_sometimes")


def test_research_notification_policy_is_deprecated_compatibility_fallback(monkeypatch) -> None:
    monkeypatch.delenv("OPERATION_NOTIFICATION_POLICY", raising=False)
    monkeypatch.setenv("RESEARCH_NOTIFICATION_POLICY", "require_delivery")

    assert resolve_operation_notification_policy() == "require_delivery"


def test_notification_diagnostics_does_not_import_research() -> None:
    path = Path("src/bithumb_bot/notification_diagnostics.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    research_imports = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Import)
            and any(alias.name == "bithumb_bot.research" or alias.name.startswith("bithumb_bot.research.") for alias in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "research" or node.module.startswith("research."))
        )
    ]

    assert research_imports == []


def test_notification_diagnostics_does_not_send_when_probe_is_false(monkeypatch) -> None:
    def unexpected_notification(*args, **kwargs):
        raise AssertionError("probe=False must not send a notification")

    monkeypatch.setattr(notification_diagnostics, "notify", unexpected_notification)

    payload = notification_diagnostics.notification_diagnostics_payload(probe=False)

    assert "probe_result" not in payload
