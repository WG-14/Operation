from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .research.hashing import sha256_prefixed


class DBSnapshotManifestError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_live_sqlite_snapshot_manifest(db_path: str | Path, *, snapshot_policy: str = "hash_manifest") -> dict[str, object]:
    path = Path(db_path).expanduser()
    if not path.exists():
        raise DBSnapshotManifestError("db_snapshot_manifest_db_missing")
    files: list[dict[str, object]] = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        exists = candidate.exists()
        item: dict[str, object] = {
            "path": str(candidate),
            "exists": exists,
            "size_bytes": 0,
            "mtime_ns": 0,
            "sha256": "",
        }
        if exists:
            stat = candidate.stat()
            item.update(
                {
                    "size_bytes": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "sha256": _sha256_file(candidate),
                }
            )
        files.append(item)
    if not files[0]["sha256"]:
        raise DBSnapshotManifestError("db_snapshot_manifest_db_hash_missing")
    payload = {
        "artifact_type": "live_sqlite_snapshot_manifest",
        "db_path": str(path),
        "db_files": files,
        "snapshot_policy": snapshot_policy,
        "full_copy_performed": False,
    }
    payload["db_snapshot_hash"] = sha256_prefixed(payload)
    return payload


def require_full_copy_disk_capacity(db_path: str | Path, destination_dir: str | Path, *, safety_multiplier: float = 2.0) -> None:
    path = Path(db_path).expanduser()
    destination = Path(destination_dir).expanduser()
    required = int(path.stat().st_size * float(safety_multiplier))
    free = int(shutil.disk_usage(destination).free)
    if free < required:
        raise DBSnapshotManifestError(
            f"db_snapshot_full_copy_disk_capacity_insufficient:required={required}:free={free}"
        )
