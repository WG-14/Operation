from __future__ import annotations

from bithumb_bot.research.backtest_common import metrics_v2_ledgers_from_trades


def test_daily_fallback_execution_record_keeps_entry_signal_source() -> None:
    _, _, records, _ = metrics_v2_ledgers_from_trades(
        trades=[
            {
                "side": "BUY",
                "qty": 1.0,
                "price": 100.0,
                "fee": 1.0,
                "fill_ts": 1_704_031_200_000,
                "entry_signal_source": "daily_participation_fallback",
                "execution": {
                    "side": "BUY",
                    "fill_status": "filled",
                    "filled_qty": 1.0,
                    "avg_fill_price": 100.0,
                    "fee": 1.0,
                    "fill_reference_ts": 1_704_031_200_000,
                },
            }
        ]
    )

    assert records[0].ts == 1_704_031_200_000
    assert records[0].entry_signal_source == "daily_participation_fallback"
