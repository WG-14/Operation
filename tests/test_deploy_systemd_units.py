from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = REPO_ROOT / "deploy/systemd"
CANONICAL_UNITS = {
    "operation-paper.service",
    "operation-healthcheck.service",
    "operation-healthcheck.timer",
    "operation-backup.service",
    "operation-backup.timer",
}


def _read(path: Path) -> ConfigParser:
    parser = ConfigParser()
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def test_supported_deployment_units_are_paper_only_and_canonical() -> None:
    names = {path.name for path in SYSTEMD_DIR.glob("*.service")} | {path.name for path in SYSTEMD_DIR.glob("*.timer")}
    assert CANONICAL_UNITS <= names
    assert "operation.service" not in names

    paper = _read(SYSTEMD_DIR / "operation-paper.service")["Service"]
    assert "MODE=paper" in paper["Environment"]
    assert paper["ExecStart"] == "@OPERATION_UV_BIN@ run operation run"
    assert "--short" not in paper["ExecStart"]
    assert "--long" not in paper["ExecStart"]


def test_healthcheck_and_backup_follow_paper_service() -> None:
    health_unit = _read(SYSTEMD_DIR / "operation-healthcheck.service")
    health = health_unit["Service"]
    assert health_unit["Unit"]["After"] == "operation-paper.service"
    assert "MODE=paper" in health["Environment"]
    assert "OPERATION_ENV_FILE=@OPERATION_ENV_FILE_PAPER@" in health["Environment"]

    backup = _read(SYSTEMD_DIR / "operation-backup.service")["Service"]
    assert "MODE=paper" in backup["Environment"]
    assert backup["ExecStart"] == "/usr/bin/env bash @OPERATION_BOT_ROOT@/scripts/backup_sqlite.sh"


def test_units_do_not_hardcode_repository_paths() -> None:
    for path in SYSTEMD_DIR.glob("*"):
        if path.suffix not in {".service", ".timer"}:
            continue
        content = path.read_text(encoding="utf-8")
        assert "/workspace/operation-bot" not in content
        assert "/home/ec2-user/operation-bot" not in content
