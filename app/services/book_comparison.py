"""Side-by-side comparison of the CEX market-making book and the on-chain LP book."""

from __future__ import annotations

from datetime import datetime

from app.models.domain import PnLSnapshot
from app.models.v3 import BookComparison, BookSide, LpPositionState

_TIE_TOLERANCE_PCT = 1e-6


def _delta(end: float, start: float | None) -> float:
    return end - (start or 0.0)


def compare_books(
    *,
    cex_start: PnLSnapshot | None,
    cex_end: PnLSnapshot | None,
    cex_capital: float,
    cex_symbol: str,
    lp_start: LpPositionState | None,
    lp_end: LpPositionState | None,
    lp_pool_label: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> BookComparison:
    """Compare both books over a window; ``*_start`` of None means "since inception"."""
    if cex_end is not None:
        cex_realized = _delta(cex_end.realized_pnl, cex_start.realized_pnl if cex_start else None)
        cex_unrealized = _delta(
            cex_end.unrealized_pnl, cex_start.unrealized_pnl if cex_start else None
        )
        cex_fees = _delta(cex_end.total_fees, cex_start.total_fees if cex_start else None)
        cex_net = _delta(cex_end.total_pnl, cex_start.total_pnl if cex_start else None)
    else:
        cex_realized = cex_unrealized = cex_fees = cex_net = 0.0

    cex = BookSide(
        label=f"CEX market making ({cex_symbol})",
        capital_quote=cex_capital,
        revenue_quote=cex_realized,
        costs_quote=cex_fees,
        inventory_effect_quote=cex_unrealized,
        net_pnl_quote=cex_net,
        return_pct=cex_net / cex_capital * 100 if cex_capital else 0.0,
        details={
            "realized_spread_capture_quote": cex_realized,
            "maker_fees_quote": cex_fees,
            "unrealized_inventory_quote": cex_unrealized,
        },
    )

    if lp_end is not None:

        def field(name: str) -> float:
            return _delta(getattr(lp_end, name), getattr(lp_start, name) if lp_start else None)

        fees = field("fees_quote")
        swap_cost = field("swap_cost_quote")
        gas = field("gas_cost_quote")
        il = field("il_quote")
        hodl = field("hodl_pnl_quote")
        net = field("net_pnl_quote")
        capital = lp_end.capital_quote
    else:
        fees = swap_cost = gas = il = hodl = net = 0.0
        capital = 0.0

    lp = BookSide(
        label=f"Uniswap V3 LP ({lp_pool_label})",
        capital_quote=capital,
        revenue_quote=fees,
        costs_quote=swap_cost + gas,
        inventory_effect_quote=il + hodl,
        net_pnl_quote=net,
        return_pct=net / capital * 100 if capital else 0.0,
        details={
            "fees_quote": fees,
            "swap_cost_quote": swap_cost,
            "gas_cost_quote": gas,
            "impermanent_loss_quote": il,
            "hodl_revaluation_quote": hodl,
        },
    )

    notes = [
        "Returns are compared on each book's own starting capital (return on capital).",
        "Both books are paper simulations; neither result predicts live performance.",
    ]
    base_asset = cex_symbol.split("/")[0]
    if base_asset not in {"ETH", "WETH"}:
        notes.append(
            f"Asset mismatch: CEX book trades {cex_symbol} while the LP book holds WETH/USDC; "
            "set SYMBOL=ETH/USDT for a closer comparison."
        )
    if cex_symbol.endswith("/USDT"):
        notes.append(
            "Quote mismatch: CEX book is USDT-quoted, LP book is USDC-quoted (basis not hedged)."
        )

    diff = cex.return_pct - lp.return_pct
    winner = "tie" if abs(diff) <= _TIE_TOLERANCE_PCT else ("cex" if diff > 0 else "lp")
    return BookComparison(
        window_start=window_start,
        window_end=window_end,
        cex=cex,
        lp=lp,
        mismatch_notes=tuple(notes),
        winner=winner,
    )
