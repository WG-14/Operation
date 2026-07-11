from __future__ import annotations

from pathlib import Path


def test_operation_excludes_research_stage_timing_command() -> None:
    root = Path(__file__).resolve().parents[1]

    assert not (root / "scripts" / "extract_research_stage_timings.py").exists()
