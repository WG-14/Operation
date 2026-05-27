from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResumeGatePreparation:
    startup_gate_reason: str | None
    initial_reconcile_halt_cleared: bool
    live_execution_broker_halt_cleared: bool
    risk_state_mismatch_halt_cleared: bool


@dataclass(frozen=True)
class RuntimeRecoveryGateService:
    """Small service boundary for recovery/readiness gate orchestration."""

    startup_gate_evaluator: Callable[[], str | None]
    stale_initial_reconcile_halt_clearer: Callable[[], bool]
    stale_live_execution_broker_halt_clearer: Callable[..., bool]
    stale_risk_state_mismatch_halt_clearer: Callable[..., bool]
    state_snapshot: Callable[[], object]
    startup_gate_reason_classifier: Callable[..., tuple[str, str]]
    resume_blocker_factory: Callable[..., object]

    def prepare_resume_gate(self) -> ResumeGatePreparation:
        initial_cleared = bool(self.stale_initial_reconcile_halt_clearer())
        startup_gate_reason = self.startup_gate_evaluator()
        broker_cleared = bool(
            self.stale_live_execution_broker_halt_clearer(
                startup_gate_reason=startup_gate_reason
            )
        )
        risk_cleared = bool(
            self.stale_risk_state_mismatch_halt_clearer(
                startup_gate_reason=startup_gate_reason
            )
        )
        startup_gate_reason = self.startup_gate_evaluator()
        return ResumeGatePreparation(
            startup_gate_reason=startup_gate_reason,
            initial_reconcile_halt_cleared=initial_cleared,
            live_execution_broker_halt_cleared=broker_cleared,
            risk_state_mismatch_halt_cleared=risk_cleared,
        )

    def startup_safety_resume_blockers(self, startup_gate_reason: str | None) -> list[object]:
        if not startup_gate_reason:
            return []
        state = self.state_snapshot()
        reason_code, summary = self.startup_gate_reason_classifier(
            startup_gate_reason,
            state=state,
        )
        return [
            self.resume_blocker_factory(
                code="STARTUP_SAFETY_GATE_BLOCKED",
                detail=startup_gate_reason,
                reason_code=reason_code,
                summary=summary,
                overridable=False,
            )
        ]

    def reconcile_ok_did_not_clear_blockers(
        self,
        startup_gate_reason: str | None,
    ) -> list[object]:
        if not startup_gate_reason:
            return []
        state = self.state_snapshot()
        if getattr(state, "last_reconcile_status", None) != "ok":
            return []
        reason_code, summary = self.startup_gate_reason_classifier(
            startup_gate_reason,
            state=state,
        )
        if reason_code == "FEE_GAP_RECOVERY_REQUIRED":
            return []
        return [
            self.resume_blocker_factory(
                code="LAST_RECONCILE_DID_NOT_CLEAR_BLOCKERS",
                detail="latest reconcile reported ok but startup safety gate still blocks resume",
                reason_code=reason_code,
                summary=summary,
                overridable=False,
            )
        ]
