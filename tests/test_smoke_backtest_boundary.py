from __future__ import annotations

import backtest as smoke_backtest


def test_root_backtest_output_is_diagnostic_only_and_non_promotable(monkeypatch) -> None:
    candles = [(index * 60_000, float(100 + index)) for index in range(20)]
    monkeypatch.setattr(smoke_backtest, "load_candles", lambda limit: candles)

    result = smoke_backtest.backtest(short_n=2, long_n=4, entry="cross")

    assert result["diagnostic_only"] is True
    assert result["non_promotable"] is True
    assert result["evidence_scope"] == "smoke_only_not_manifest_backed"
