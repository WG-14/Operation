from __future__ import annotations

from pathlib import Path

import pytest

from bithumb_bot.db_snapshot_manifest import (
    DBSnapshotManifestError,
    build_live_sqlite_snapshot_manifest,
    require_full_copy_disk_capacity,
)
from bithumb_bot.h74_observation import build_h74_observation_experiment_envelope, verify_h74_observation_experiment_envelope


def test_snapshot_manifest_hashes_db_wal_shm_without_copy(tmp_path: Path) -> None:
    db = tmp_path / "live.sqlite"
    db.write_bytes(b"db")
    Path(str(db) + "-wal").write_bytes(b"wal")
    manifest = build_live_sqlite_snapshot_manifest(db)
    assert manifest["artifact_type"] == "live_sqlite_snapshot_manifest"
    assert manifest["full_copy_performed"] is False
    assert manifest["db_files"][0]["sha256"].startswith("sha256:")
    assert manifest["db_files"][1]["sha256"].startswith("sha256:")
    assert manifest["db_files"][2]["exists"] is False


def test_snapshot_manifest_is_accepted_by_h74_experiment_envelope(tmp_path: Path) -> None:
    db = tmp_path / "live.sqlite"
    db.write_bytes(b"db")
    manifest = build_live_sqlite_snapshot_manifest(db)
    envelope = build_h74_observation_experiment_envelope(
        experiment_run_id="exp",
        runtime_git_commit_sha="commit",
        runtime_git_clean=True,
        env_hash="sha256:" + "1" * 64,
        strategy_revision_id="sha256:" + "2" * 64,
        risk_scope_id="sha256:" + "3" * 64,
        risk_baseline_certificate_hash="sha256:" + "4" * 64,
        starting_broker_position={"qty": 0},
        starting_local_position={"qty": 0},
        db_snapshot_hash=str(manifest["db_snapshot_hash"]),
        included_history_policy="declared_live_history_scope",
    )
    verify_h74_observation_experiment_envelope(envelope)


def test_full_copy_requires_disk_capacity_precheck(tmp_path: Path) -> None:
    db = tmp_path / "live.sqlite"
    db.write_bytes(b"x" * 1024)
    with pytest.raises(DBSnapshotManifestError, match="disk_capacity_insufficient"):
        require_full_copy_disk_capacity(db, tmp_path, safety_multiplier=10**18)


def test_missing_db_snapshot_hash_and_locator_fails() -> None:
    with pytest.raises(Exception, match="db_snapshot_hash_or_locator"):
        build_h74_observation_experiment_envelope(
            experiment_run_id="exp",
            runtime_git_commit_sha="commit",
            runtime_git_clean=True,
            env_hash="sha256:" + "1" * 64,
            strategy_revision_id="sha256:" + "2" * 64,
            risk_scope_id="sha256:" + "3" * 64,
            risk_baseline_certificate_hash="sha256:" + "4" * 64,
            starting_broker_position={"qty": 0},
            starting_local_position={"qty": 0},
            included_history_policy="declared_live_history_scope",
        )
