from __future__ import annotations

import pytest

from bithumb_bot.cli.main import main


def test_paired_experiment_help_is_registered(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["paired-experiment", "--help"])

    assert exc.value.code == 0
    assert "paired-experiment" in capsys.readouterr().out
