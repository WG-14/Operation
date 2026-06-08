from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/runbooks/forward-return-diagnostics.md"


def test_runbook_documents_signal_close_mfe_mae_semantics() -> None:
    source = RUNBOOK.read_text(encoding="utf-8")

    assert "`signal_close` is a diagnostic convenience only" in source
    assert "path_start_policy=next_candle_after_signal_close" in source
    assert "intrabar_included=false" in source
    assert "mfe_mae_basis=ohlc_future_candles_only" in source
    assert "MFE/MAE therefore" in source
