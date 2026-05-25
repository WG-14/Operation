from __future__ import annotations

import sqlite3

from .broker.order_rules import get_effective_order_rules
from .dust import build_dust_display_context, build_executable_lot
from .fee_authority import build_fee_authority_snapshot
from .lifecycle import (
    OPEN_POSITION_STATE,
    mark_harmless_dust_positions,
    reclassify_non_executable_open_exposure,
)


def load_last_reconcile_metadata(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            "SELECT last_reconcile_metadata FROM bot_health WHERE id=1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    return str(row[0])


class PositionStateNormalizer:
    """Explicit pre-decision persistence boundary for position state repairs."""

    def normalize_and_persist(
        self,
        conn: sqlite3.Connection,
        *,
        pair: str,
        market_price: float,
        slippage_bps: float,
        entry_edge_buffer_ratio: float,
    ) -> int:
        dust_context = build_dust_display_context(load_last_reconcile_metadata(conn))
        updated = 0
        try:
            updated += int(
                mark_harmless_dust_positions(
                    conn,
                    pair=pair,
                    dust_metadata=dust_context,
                )
            )
        except sqlite3.OperationalError:
            pass

        resolution = get_effective_order_rules(pair)
        rules = resolution.rules
        fee_authority = build_fee_authority_snapshot(resolution)
        try:
            row = conn.execute(
                """
                SELECT
                    SUM(qty_open) AS qty_open
                FROM open_position_lots
                WHERE pair=? AND position_state=? AND qty_open > 1e-12
                  AND COALESCE(position_semantic_basis, '')='lot-native'
                  AND COALESCE(executable_lot_count, 0) > 0
                  AND COALESCE(dust_tracking_lot_count, 0) = 0
                """,
                (pair, OPEN_POSITION_STATE),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        qty_open = float(row[0]) if row is not None and row[0] is not None else 0.0
        if qty_open > 1e-12:
            executable_lot = build_executable_lot(
                qty=qty_open,
                market_price=float(market_price),
                min_qty=float(rules.min_qty),
                qty_step=float(rules.qty_step),
                min_notional_krw=float(rules.min_notional_krw),
                max_qty_decimals=int(rules.max_qty_decimals),
                exit_fee_ratio=float(fee_authority.taker_ask_fee_rate),
                exit_slippage_bps=float(slippage_bps),
                exit_buffer_ratio=float(entry_edge_buffer_ratio),
            )
            if executable_lot.executable_qty <= 1e-12:
                try:
                    updated += int(
                        reclassify_non_executable_open_exposure(
                            conn,
                            pair=pair,
                            executable_lot=executable_lot,
                        )
                    )
                except sqlite3.OperationalError:
                    pass
        if updated > 0:
            conn.commit()
        return updated
