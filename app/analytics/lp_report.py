"""JSON-safe serializers for V3 pool, LP book and comparison objects.

Integers that can exceed 2**53 (liquidity, sqrt prices, raw token amounts) are
emitted as decimal strings so browsers and SQLite never lose precision.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime

from app.market_data.uniswap_v3.pricing import tick_to_price
from app.market_data.uniswap_v3.tick_bitmap import initialized_ticks_in_words
from app.models.v3 import (
    BookComparison,
    BookSide,
    LpPositionState,
    LpResult,
    RebalanceEvent,
    SwapQuote,
    V3PoolSnapshot,
)

_BIG_INT_FIELDS = {
    "liquidity",
    "amount_base",
    "amount_quote",
    "idle_base",
    "idle_quote",
    "fees_owed_base",
    "fees_owed_quote",
    "swap_amount_in",
    "swap_amount_out",
}


def _plain(obj) -> dict:
    out: dict = {}
    for item in fields(obj):
        value = getattr(obj, item.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif item.name in _BIG_INT_FIELDS and isinstance(value, int):
            value = str(value)
        out[item.name] = value
    return out


def lp_state_to_dict(state: LpPositionState) -> dict:
    payload = _plain(state)
    payload["time_in_range_pct"] = (
        state.time_in_range_sec / state.time_tracked_sec * 100 if state.time_tracked_sec else None
    )
    payload["return_on_capital_pct"] = (
        state.net_pnl_quote / state.capital_quote * 100 if state.capital_quote else 0.0
    )
    return payload


def lp_state_from_dict(raw: dict) -> LpPositionState:
    values = {}
    for item in fields(LpPositionState):
        value = raw.get(item.name)
        if item.name in _BIG_INT_FIELDS and value is not None:
            value = int(value)
        elif item.name == "timestamp":
            value = datetime.fromisoformat(value)
        values[item.name] = value
    return LpPositionState(**values)


def rebalance_to_dict(event: RebalanceEvent) -> dict:
    return _plain(event)


def rebalance_from_dict(raw: dict) -> RebalanceEvent:
    values = {}
    for item in fields(RebalanceEvent):
        value = raw.get(item.name)
        if item.name in _BIG_INT_FIELDS and value is not None:
            value = int(value)
        elif item.name == "timestamp":
            value = datetime.fromisoformat(value)
        values[item.name] = value
    return RebalanceEvent(**values)


def lp_result_to_dict(result: LpResult) -> dict:
    return _plain(result)


def _price_kwargs(snapshot: V3PoolSnapshot) -> dict:
    meta = snapshot.metadata
    return dict(
        base_is_token0=meta.base_is_token0,
        token0_decimals=meta.token0_decimals,
        token1_decimals=meta.token1_decimals,
    )


def liquidity_distribution(snapshot: V3PoolSnapshot, *, max_buckets: int = 120) -> list[dict]:
    """Active liquidity per price bucket around the current tick, from the loaded window."""
    window = snapshot.window
    if window is None:
        return []
    spacing = window.tick_spacing
    initialized = initialized_ticks_in_words(window.bitmap_words, spacing)
    if not initialized:
        return []
    # Bucket width: whole tick spacings, covering roughly +/-6% of price in total.
    bucket_ticks = max(spacing, (1200 // max_buckets) // spacing * spacing)
    start = (snapshot.tick // bucket_ticks - max_buckets // 2) * bucket_ticks
    edges = [start + i * bucket_ticks for i in range(max_buckets + 1)]

    # Walk liquidity from the current tick outward using liquidityNet.
    def liquidity_at(tick: int) -> int:
        liquidity = snapshot.liquidity
        if tick >= snapshot.tick:
            for t in initialized:
                if snapshot.tick < t <= tick and t in window.ticks:
                    liquidity += window.ticks[t].liquidity_net
        else:
            for t in reversed(initialized):
                if tick < t <= snapshot.tick and t in window.ticks:
                    liquidity -= window.ticks[t].liquidity_net
        return max(liquidity, 0)

    kwargs = _price_kwargs(snapshot)
    buckets = []
    for lower, upper in zip(edges, edges[1:], strict=False):
        p1, p2 = tick_to_price(lower, **kwargs), tick_to_price(upper, **kwargs)
        buckets.append(
            {
                "tick_lower": lower,
                "tick_upper": upper,
                "price_lower": min(p1, p2),
                "price_upper": max(p1, p2),
                "liquidity": str(liquidity_at(lower)),
                "contains_price": lower <= snapshot.tick < upper,
            }
        )
    buckets.sort(key=lambda bucket: bucket["price_lower"])
    return buckets


def v3_snapshot_to_dict(snapshot: V3PoolSnapshot, *, include_window: bool = True) -> dict:
    meta = snapshot.metadata
    payload = {
        "pool_address": meta.pool_address,
        "fee_tier_bps": meta.fee_tier_bps,
        "tick_spacing": meta.tick_spacing,
        "block_number": snapshot.block_number,
        "block_timestamp": snapshot.block_timestamp.isoformat(),
        "price": snapshot.price,
        "tick": snapshot.tick,
        "sqrt_price_x96": str(snapshot.sqrt_price_x96),
        "liquidity": str(snapshot.liquidity),
        "base_fee_gwei": None if snapshot.base_fee_wei is None else snapshot.base_fee_wei / 1e9,
        "is_stale": snapshot.is_stale,
        "is_incomplete": snapshot.is_incomplete,
        "data_source": snapshot.data_source,
        "window": None,
    }
    if include_window and snapshot.window is not None:
        window = snapshot.window
        kwargs = _price_kwargs(snapshot)
        low_tick = window.min_word * 256 * window.tick_spacing
        high_tick = (window.max_word + 1) * 256 * window.tick_spacing
        p1, p2 = tick_to_price(low_tick, **kwargs), tick_to_price(high_tick, **kwargs)
        payload["window"] = {
            "block_number": window.block_number,
            "is_current": snapshot.window_is_current,
            "price_low": min(p1, p2),
            "price_high": max(p1, p2),
            "initialized_tick_count": len(window.ticks),
            "distribution": liquidity_distribution(snapshot),
        }
    return payload


def swap_quote_to_dict(
    quote: SwapQuote, snapshot: V3PoolSnapshot, *, side: str, amount_in_human: float
) -> dict:
    meta = snapshot.metadata
    kwargs = _price_kwargs(snapshot)
    sells_base = side == "sell_base"
    in_decimals = meta.base_decimals if sells_base else meta.quote_decimals
    out_decimals = meta.quote_decimals if sells_base else meta.base_decimals
    amount_out = quote.amount_out / 10**out_decimals
    consumed = quote.amount_in_consumed / 10**in_decimals
    price_after = tick_to_price(quote.tick_after, **kwargs)
    execution_price = None
    if consumed > 0 and amount_out > 0:
        execution_price = amount_out / consumed if sells_base else consumed / amount_out
    return {
        "side": side,
        "amount_in": amount_in_human,
        "amount_in_consumed": consumed,
        "amount_out": amount_out,
        "amount_in_raw": str(quote.amount_in),
        "amount_out_raw": str(quote.amount_out),
        "fee_paid": quote.fee_paid / 10**in_decimals,
        "price_before": snapshot.price,
        "price_after": price_after,
        "execution_price": execution_price,
        "price_impact_bps": abs(price_after - snapshot.price) / snapshot.price * 10_000,
        "ticks_crossed": quote.ticks_crossed,
        "limit_reason": quote.limit_reason,
        "block_number": snapshot.block_number,
        "window_block_number": snapshot.window.block_number if snapshot.window else None,
        "exact": quote.exact,
    }


def _side_to_dict(side: BookSide) -> dict:
    return {
        "label": side.label,
        "capital_quote": side.capital_quote,
        "revenue_quote": side.revenue_quote,
        "costs_quote": side.costs_quote,
        "inventory_effect_quote": side.inventory_effect_quote,
        "net_pnl_quote": side.net_pnl_quote,
        "return_pct": side.return_pct,
        "details": side.details,
    }


def comparison_to_dict(comparison: BookComparison) -> dict:
    return {
        "window_start": comparison.window_start.isoformat() if comparison.window_start else None,
        "window_end": comparison.window_end.isoformat() if comparison.window_end else None,
        "cex": _side_to_dict(comparison.cex),
        "lp": _side_to_dict(comparison.lp),
        "mismatch_notes": list(comparison.mismatch_notes),
        "winner": comparison.winner,
    }
