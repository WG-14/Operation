from __future__ import annotations

from pathlib import Path

import pytest

from bithumb_bot.paths import PathManager, PathPolicyError
from bithumb_bot.research.hashing import report_content_hash_payload, sha256_prefixed
from bithumb_bot.research.report_writer import finalize_research_report_payload, write_research_report
from tests.factories.research_reports import minimal_research_report


def test_research_entrypoints_do_not_implicitly_load_repo_dotenv() -> None:
    assert "load_dotenv" not in Path("backtest2.py").read_text(encoding="utf-8")
    research_cli = Path("src/bithumb_bot/research/cli.py").read_text(encoding="utf-8")
    assert ".env" not in research_cli


def test_research_outputs_reject_repo_internal_data_root(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "paper")
    monkeypatch.setenv("DATA_ROOT", "research-output")
    for key in ("ENV_ROOT", "RUN_ROOT", "LOG_ROOT", "BACKUP_ROOT", "ARCHIVE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    manager = PathManager.from_env(Path.cwd())

    with pytest.raises(PathPolicyError, match="outside repository"):
        write_research_report(
            manager=manager,
            experiment_id="repo_internal",
            report_name="backtest",
            payload={
                "experiment_id": "repo_internal",
                "candidates": [],
                "generated_at": "2026-05-03T00:00:00+00:00",
            },
        )


def test_research_report_finalizer_adds_stable_artifact_refs_paths_and_content_hash(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "paper")
    for key in ("ENV_ROOT", "RUN_ROOT", "DATA_ROOT", "LOG_ROOT", "BACKUP_ROOT", "ARCHIVE_ROOT"):
        monkeypatch.setenv(key, str(tmp_path / f"{key.lower()}_root"))
    manager = PathManager.from_env(Path.cwd())

    paths, finalized, content_hash = finalize_research_report_payload(
        manager=manager,
        experiment_id="finalizer_contract",
        report_name="walk_forward",
        payload=minimal_research_report(report_kind="walk_forward", experiment_id="finalizer_contract"),
    )

    assert finalized["content_hash"] == content_hash
    assert finalized["artifact_refs"]["report"] == "reports/research/finalizer_contract/walk_forward_report.json"
    assert finalized["artifact_refs"]["candidate_events"] == "derived/research/finalizer_contract/candidate_events.jsonl"
    assert finalized["artifact_paths"]["report_path"] == str(paths.report_path.resolve())
    assert finalized["artifact_paths"]["derived_path"] == str(paths.derived_path.resolve())
    assert sha256_prefixed(report_content_hash_payload(finalized)) == content_hash


def test_research_report_refs_derived_candidates_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODE", "paper")
    for key in ("ENV_ROOT", "RUN_ROOT", "DATA_ROOT", "LOG_ROOT", "BACKUP_ROOT", "ARCHIVE_ROOT"):
        monkeypatch.setenv(key, str(tmp_path / f"{key.lower()}_root"))
    manager = PathManager.from_env(Path.cwd())
    payload = minimal_research_report(report_kind="backtest", experiment_id="ref_contract")
    payload.setdefault("research_run", {})["report_detail"] = "summary"

    result = write_research_report(
        manager=manager,
        experiment_id="ref_contract",
        report_name="backtest",
        payload=payload,
    )

    persisted = result.paths.report_path.read_text(encoding="utf-8")
    assert '"derived_candidates": "derived/research/ref_contract/backtest_candidates.json"' in persisted
    assert result.paths.derived_path.exists()
