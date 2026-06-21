from app.analytics.backtest_metrics import BacktestMetrics
from app.models.domain import (
    AmmPoolSnapshot,
    Fill,
    Opportunity,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
)


def format_performance_report(
    *,
    tick: int,
    snapshot: OrderBookSnapshot | None,
    position: Position | None,
    pnl: PnLSnapshot | None,
    open_quotes: int,
    kill_switch_active: bool,
    pool_snapshot: AmmPoolSnapshot | None = None,
    compare_mid: float | None = None,
    opportunities: list[Opportunity] | None = None,
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

    if compare_mid is not None and pool_snapshot is not None:
        stale = " (stale)" if pool_snapshot.is_stale else ""
        spread_bps = 0.0
        if compare_mid > 0:
            spread_bps = abs(pool_snapshot.spot_price - compare_mid) / compare_mid * 10_000
        lines.append(
            f"DEX{stale}: cex_mid={compare_mid:.2f} "
            f"amm={pool_snapshot.spot_price:.2f} spread={spread_bps:.2f}bps"
        )

    if opportunities:
        latest = opportunities[0]
        lines.append(
            f"Opportunity: {latest.direction.value} net_edge={latest.net_edge_bps:.2f}bps"
        )

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
    pool_snapshot: AmmPoolSnapshot | None = None,
    compare_mid: float | None = None,
    opportunities: list[Opportunity] | None = None,
) -> dict:
    best_bid = snapshot.bids[0].price if snapshot and snapshot.bids else None
    best_ask = snapshot.asks[0].price if snapshot and snapshot.asks else None
    mid = None
    spread_bps = None
    if best_bid is not None and best_ask is not None:
        mid = (best_bid + best_ask) / 2
        if mid > 0:
            spread_bps = ((best_ask - best_bid) / mid) * 10_000

    dex_spread_bps = None
    if compare_mid is not None and pool_snapshot is not None and compare_mid > 0:
        dex_spread_bps = abs(pool_snapshot.spot_price - compare_mid) / compare_mid * 10_000

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
        "dex": {
            "cex_compare_mid": compare_mid,
            "amm_price": pool_snapshot.spot_price if pool_snapshot else None,
            "spread_bps": dex_spread_bps,
            "is_stale": pool_snapshot.is_stale if pool_snapshot else None,
            "pool_address": pool_snapshot.pool_address if pool_snapshot else None,
        },
        "opportunities": [
            {
                "direction": opportunity.direction.value,
                "net_edge_bps": opportunity.net_edge_bps,
                "net_edge": opportunity.net_edge,
                "trial_trade_size": opportunity.trial_trade_size,
                "timestamp": opportunity.timestamp.isoformat(),
            }
            for opportunity in (opportunities or [])
        ],
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


def opportunity_to_dict(opportunity: Opportunity) -> dict:
    return {
        "direction": opportunity.direction.value,
        "cex_mid": opportunity.cex_mid,
        "amm_price": opportunity.amm_price,
        "trial_trade_size": opportunity.trial_trade_size,
        "gross_edge": opportunity.gross_edge,
        "cex_fee": opportunity.cex_fee,
        "amm_fee": opportunity.amm_fee,
        "gas_cost": opportunity.gas_cost,
        "slippage_cost": opportunity.slippage_cost,
        "net_edge": opportunity.net_edge,
        "net_edge_bps": opportunity.net_edge_bps,
        "timestamp": opportunity.timestamp.isoformat(),
    }


def fill_to_dict(fill: Fill) -> dict:
    return {
        "symbol": fill.symbol,
        "side": fill.side.value,
        "price": fill.price,
        "size": fill.size,
        "fee": fill.fee,
        "quote_id": fill.quote_id,
        "timestamp": fill.timestamp.isoformat(),
    }


def pnl_history_point(pnl: PnLSnapshot) -> dict:
    return {
        "symbol": pnl.symbol,
        "total_pnl": pnl.total_pnl,
        "realized_pnl": pnl.realized_pnl,
        "unrealized_pnl": pnl.unrealized_pnl,
        "total_fees": pnl.total_fees,
        "timestamp": pnl.timestamp.isoformat(),
    }


def format_backtest_report(metrics: BacktestMetrics, fills: list[Fill]) -> str:
    lines = [
        "=== Backtest Report ===",
        f"Ticks: {metrics.tick_count}",
        f"Fills: {metrics.fill_count}",
        f"Quotes submitted: {metrics.quote_count}",
        f"Fill rate: {metrics.fill_rate:.2%}",
        f"Total PnL: {metrics.total_pnl:.4f}",
        f"Max drawdown: {metrics.max_drawdown:.4f}",
        f"Sharpe ratio (annualized): {metrics.sharpe_ratio:.4f}",
        f"Final base: {metrics.final_base:.6f}",
        f"Final quote: {metrics.final_quote:.2f}",
        f"Trade log entries: {len(fills)}",
    ]
    return "\n".join(lines)


def backtest_metrics_dict(metrics: BacktestMetrics) -> dict:
    return {
        "tick_count": metrics.tick_count,
        "fill_count": metrics.fill_count,
        "quote_count": metrics.quote_count,
        "fill_rate": metrics.fill_rate,
        "total_pnl": metrics.total_pnl,
        "max_drawdown": metrics.max_drawdown,
        "sharpe_ratio": metrics.sharpe_ratio,
        "final_base": metrics.final_base,
        "final_quote": metrics.final_quote,
    }
