from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/runbooks/forward-return-diagnostics.md"


REQUIRED_POLICY_LINES = (
    "forward-return diagnostics output must not be used as strategy promotion evidence",
    "forward-return diagnostics output must not be used as approved profile evidence",
    "forward-return diagnostics output must not be used as live readiness evidence",
    "forward-return diagnostics output must not be used as capital allocation evidence",
)


def test_forward_diagnostics_runbook_exists() -> None:
    assert RUNBOOK.exists()


def test_forward_diagnostics_runbook_declares_diagnostic_only() -> None:
    source = RUNBOOK.read_text(encoding="utf-8")

    assert "Diagnostic-only policy" in source
    for line in REQUIRED_POLICY_LINES:
        assert line in source


def test_forward_diagnostics_docs_forbid_promotion_evidence_use() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for line in REQUIRED_POLICY_LINES:
        assert line in runbook
    forbidden_positive_claims = (
        "promotion-ready",
        "approved-profile-ready",
        "capital allocation ready",
        "forward diagnostics approval",
    )
    for claim in forbidden_positive_claims:
        assert claim not in runbook
    assert "live-ready" not in runbook
