from __future__ import annotations

import pytest

from operation.execution_authority import (
    execution_authority_from_payload,
    require_authority_operation,
)
from operation.operator_smoke_authority import OPERATOR_SMOKE_AUTHORITY_ARTIFACT_TYPE


def test_execution_authority_scopes_are_not_interchangeable() -> None:
    approved = execution_authority_from_payload(
        {"artifact_type": "approved_profile", "profile_content_hash": "sha256:approved", "market": "KRW-BTC"}
    )
    smoke = execution_authority_from_payload(
        {"artifact_type": OPERATOR_SMOKE_AUTHORITY_ARTIFACT_TYPE, "market": "KRW-BTC", "max_notional_krw": 50_000}
    )
    closeout = execution_authority_from_payload(
        {"artifact_type": "emergency_closeout_authority", "market": "KRW-BTC", "notional_cap": 50_000}
    )

    require_authority_operation(approved, "strategy_run")
    require_authority_operation(smoke, "operator_smoke_buy")
    require_authority_operation(closeout, "position_reduction")
    for authority, forbidden_operation in (
        (approved, "operator_smoke_buy"),
        (smoke, "strategy_run"),
        (closeout, "strategy_run"),
    ):
        with pytest.raises(ValueError, match="operation_not_allowed"):
            require_authority_operation(authority, forbidden_operation)


def test_unknown_authority_artifact_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown_execution_authority_type"):
        execution_authority_from_payload({"artifact_type": "unknown_observation_artifact"})
