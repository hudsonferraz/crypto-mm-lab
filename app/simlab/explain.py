"""Plain-language texts for the simulation lab (specs/009 FR-007, FR-022)."""

from app.simlab.engine import SimSettings, SimulationResult

STRATEGY_LABELS = {
    "pure_mm": "Pure market making",
    "inventory_skew": "Inventory skew",
    "volatility_spread": "Volatility-scaled spread",
}

FILL_MODEL_LABELS = {
    "full_cross_fill": "Full cross",
    "partial_fill": "Partial (top-of-book size)",
    "latency_prob_fill": "Latency + probability",
    "calibrated_fill": "Calibrated (fitted on testnet)",
    "trade_fill": "Trade-driven (real trades)",
}

FILL_MODEL_HELP = {
    "full_cross_fill": "Fills a quote in full when a snapshot shows the other side at or "
    "through its price. Misses touches between snapshots, so it is pessimistic.",
    "partial_fill": "Like full cross, but fills at most the size shown at the best price.",
    "latency_prob_fill": "Needs the cross to last two snapshots, then fills with a 70% chance.",
    "calibrated_fill": "Adds a fitted chance of a fill between snapshots. Fitted on Binance "
    "testnet sessions with tight quotes; far from those it is a guess.",
    "trade_fill": "Fills when a real public trade printed at or through the quote's price, up "
    "to that trade's size. Optimistic: it assumes our order was first in line.",
}

DECISIONS = [
    {
        "id": "D1",
        "title": "Your runs are yours",
        "text": "Your browser holds a random id; runs belong to it. Nobody else sees your "
        "runs, and nothing here changes the live market maker. There is no login and no "
        "personal data.",
    },
    {
        "id": "D2",
        "title": "Record now, or use a bundled recording",
        "text": "No exchange gives free history of full order books. A run either records "
        "the live book for 30 to 120 seconds, or uses a real recording shipped with the app, "
        "which runs at once.",
    },
    {
        "id": "D3",
        "title": "Real pairs, real market rules",
        "text": "Pairs come from the exchange's own market list. Prices round to the market's "
        "price step (bids down, asks up, so rounding never narrows the spread) and sizes to "
        "its size step and minimum. Each quote is worth about 100 in the quote currency.",
    },
    {
        "id": "D4",
        "title": "Fills from real trades",
        "text": "The trade-driven model fills a quote when a real trade printed at or through "
        "its price. Snapshot-based models miss moves between snapshots; this one sees them, "
        "but it assumes our order was first in line.",
    },
    {
        "id": "D5",
        "title": "Limits that keep the demo up",
        "text": "The demo runs on a small free server and exchanges limit public data. So a "
        "few recordings run at once, the rest wait in line, each visitor has one run at a "
        "time and a few per hour, and runs are kept for two hours in memory. A restart "
        "clears them.",
    },
    {
        "id": "D6",
        "title": "Compared with doing nothing",
        "text": "Every run starts with 10,000 worth: half in the coin, half in cash. The "
        "result compares the strategy with simply holding that start. Market making earns "
        "spread but also carries inventory; holding shows what the market alone did.",
    },
]


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def summarize(result: SimulationResult, settings: SimSettings, quote_ccy: str) -> list[str]:
    lines = []
    move = (result.end_mid - result.start_mid) / result.start_mid * 10_000
    direction = "rose" if move > 0 else "fell" if move < 0 else "did not move"
    lines.append(
        f"Over {result.duration_sec:.0f} seconds the mid price {direction}"
        + (f" {abs(move):.1f} bps." if move else ".")
    )
    if result.fill_count == 0:
        lines.append(
            f"No quote filled at a {settings.spread_bps:g} bps spread with the "
            f"{FILL_MODEL_LABELS[settings.fill_model].lower()} model, so the result is just "
            "holding the start."
        )
    else:
        lines.append(
            f"{_count(result.fill_count, 'fill')} ({_count(result.buy_count, 'buy')}, "
            f"{_count(result.sell_count, 'sell')}), paying {result.fees:,.2f} {quote_ccy} in fees."
        )
    edge = result.edge_vs_hold
    final = f"(final {result.final_value:,.2f} against {result.hold_value:,.2f})"
    if abs(edge) < 0.005:
        lines.append(f"The strategy matched holding {final}.")
    else:
        verdict = "beat" if edge > 0 else "trailed"
        amount = f"{abs(edge):,.2f}"
        lines.append(f"The strategy {verdict} holding by {amount} {quote_ccy} {final}.")
    base_change = result.final_base - result.start_base
    if result.start_base and abs(base_change) / result.start_base > 0.05:
        more = "more" if base_change > 0 else "less"
        lines.append(
            f"It ended holding {abs(base_change) / result.start_base:.0%} {more} of the coin "
            "than at the start, so part of the result is the price move on that inventory."
        )
    lines.extend(result.notes)
    return lines
