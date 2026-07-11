from __future__ import annotations

from pathlib import Path


def test_operation_ci_runs_research_boundary_and_approval_contract_checks() -> None:
    workflow_paths = sorted(Path(".github/workflows").glob("*.yml")) + sorted(
        Path(".github/workflows").glob("*.yaml")
    )
    assert workflow_paths

    matching_commands: list[str] = []
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        if "tests/test_operation_research_boundary.py" not in text:
            continue
        matching_commands.append(text)

    assert matching_commands, "Operation CI must verify the no-research boundary"
    assert any("tests/test_operation_approval.py" in text for text in matching_commands)
