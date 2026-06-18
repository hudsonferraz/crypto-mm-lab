from app.models.domain import OrderBookSnapshot, PnLSnapshot, Position


def format_performance_report(
    *,
    tick: int,
    snapshot: OrderBookSnapshot | None,
    position: Position | None,
    pnl: PnLSnapshot | None,
    open_quotes: int,
    kill_switch_active: bool,
) -> str:
    lines = [
        f"Tick {tick}",
        f"Kill switch: {'ON' if kill_switch_active else 'OFF'}",
        f"Open quotes: {open_quotes}",
    ]

    if snapshot is not None:
        best_bid = snapshot.bids[0].price if snapshot.bids else None
        best_ask = snapshot.asks[0].price if snapshot.asks else None
        stale = " (stale)" if snapshot.is_stale else ""
        lines.append(f"Market{stale}: bid={best_bid} ask={best_ask}")

    if position is not None:
        lines.append(
            f"Position: base={position.base_amount:.6f} "
            f"quote={position.quote_amount:.2f} "
            f"avg_entry={position.average_entry_price:.2f}"
        )

    if pnl is not None:
        lines.append(
            f"PnL: realized={pnl.realized_pnl:.4f} "
            f"unrealized={pnl.unrealized_pnl:.4f} "
            f"fees={pnl.total_fees:.4f} "
            f"total={pnl.total_pnl:.4f}"
        )

    return "\n".join(lines)


def performance_report_dict(
    *,
    tick: int,
    running: bool,
    snapshot: OrderBookSnapshot | None,
    position: Position | None,
    pnl: PnLSnapshot | None,
    open_quotes: int,
    kill_switch_active: bool,
    last_tick_at: str | None,
) -> dict:
    best_bid = snapshot.bids[0].price if snapshot and snapshot.bids else None
    best_ask = snapshot.asks[0].price if snapshot and snapshot.asks else None
    mid = None
    spread_bps = None
    if best_bid is not None and best_ask is not None:
        mid = (best_bid + best_ask) / 2
        if mid > 0:
            spread_bps = ((best_ask - best_bid) / mid) * 10_000

    return {
        "tick": tick,
        "running": running,
        "last_tick_at": last_tick_at,
        "kill_switch_active": kill_switch_active,
        "open_quotes": open_quotes,
        "market": {
            "symbol": snapshot.symbol if snapshot else None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "spread_bps": spread_bps,
            "is_stale": snapshot.is_stale if snapshot else None,
        },
        "position": {
            "base_amount": position.base_amount if position else None,
            "quote_amount": position.quote_amount if position else None,
            "average_entry_price": position.average_entry_price if position else None,
        },
        "pnl": {
            "realized": pnl.realized_pnl if pnl else None,
            "unrealized": pnl.unrealized_pnl if pnl else None,
            "fees": pnl.total_fees if pnl else None,
            "total": pnl.total_pnl if pnl else None,
        },
    }
