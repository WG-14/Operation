from __future__ import annotations

import os


OPERATION_NOTIFICATION_POLICIES = frozenset({"best_effort", "require_delivery", "disabled"})


def resolve_operation_notification_policy(policy: str | None = None) -> str:
    """Resolve the notification policy for operation-owned commands."""
    if policy is not None:
        raw = policy
    elif "OPERATION_NOTIFICATION_POLICY" in os.environ:
        raw = os.environ["OPERATION_NOTIFICATION_POLICY"]
    else:
        raw = "best_effort"

    normalized = str(raw or "best_effort").strip().lower()
    if normalized not in OPERATION_NOTIFICATION_POLICIES:
        allowed = ", ".join(sorted(OPERATION_NOTIFICATION_POLICIES))
        raise ValueError(f"invalid operation notification policy={raw!r}; allowed values: {allowed}")
    return normalized
