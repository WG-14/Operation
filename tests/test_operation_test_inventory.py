from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts/run_operation_tests.sh"
REQUIRED = {
    "test_exchange_removal_contract.py",
    "test_offline_paper_runtime.py",
    "test_operation_cli_surface.py",
    "test_run_lock.py",
    "test_fill_dedupe.py",
    "test_execution_quality.py",
    "test_lot_native_contract.py",
    "test_deploy_systemd_units.py",
}


def test_curated_runner_references_existing_unique_tests_and_p0_categories() -> None:
    paths = re.findall(r"tests/[A-Za-z0-9_./-]+\.py", RUNNER.read_text(encoding="utf-8"))
    assert paths
    assert len(paths) == len(set(paths))
    assert all((REPO_ROOT / path).is_file() for path in paths)
    assert REQUIRED <= {Path(path).name for path in paths}
    assert not any("bithumb" in path.lower() for path in paths)
