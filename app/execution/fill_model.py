from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal

from app.execution.fee_model import maker_fee
from app.execution.fill_calibration import FillCalibration
from app.market_data.orderbook import best_ask, best_ask_size, best_bid, best_bid_size
from app.models.domain import Fill, OrderBookSnapshot, Quote, QuoteSide

FillMode = Literal["full_cross_fill", "partial_fill", "latency_prob_fill", "calibrated_fill"]

FILL_MODE_FULL_CROSS: FillMode = "full_cross_fill"
FILL_MODE_PARTIAL: FillMode = "partial_fill"
FILL_MODE_LATENCY_PROB: FillMode = "latency_prob_fill"
FILL_MODE_CALIBRATED: FillMode = "calibrated_fill"


@dataclass
class OpenQuote:
    quote_id: str
    quote: Quote
    crossed_streak: int = 0
    # Time of the last snapshot this quote was evaluated on; None means "since the
    # quote was created". calibrated_fill measures its interval from here (specs/006 R8).
    last_evaluated_at: datetime | None = None


def _fill_size_for_cross(
    quote: Quote,
    snapshot: OrderBookSnapshot,
    fill_mode: FillMode,
) -> float | None:
    if quote.side == QuoteSide.BID:
        opposing_size = best_ask_size(snapshot)
    else:
        opposing_size = best_bid_size(snapshot)

    if fill_mode in {FILL_MODE_PARTIAL, FILL_MODE_LATENCY_PROB}:
        if opposing_size is None or opposing_size <= 0:
            return None
        return min(quote.size, opposing_size)

    return quote.size


def _deterministic_unit_random(quote_id: str, tick_index: int) -> float:
    digest = sha256(f"{quote_id}:{tick_index}".encode()).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def snapshot_mid(snapshot: OrderBookSnapshot) -> float | None:
    """Mid of the best bid and ask, or None when a side is missing."""
    bid, ask = best_bid(snapshot), best_ask(snapshot)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def detect_fills(
    open_quotes: list[OpenQuote],
    snapshot: OrderBookSnapshot,
    maker_fee_bps: float,
    *,
    fill_mode: FillMode = FILL_MODE_FULL_CROSS,
    fill_latency_ticks: int = 1,
    fill_probability: float = 1.0,
    tick_index: int = 0,
    calibration: FillCalibration | None = None,
    volatility: float | None = None,
) -> list[Fill]:
    external_bid = best_bid(snapshot)
    external_ask = best_ask(snapshot)
    if external_bid is None or external_ask is None:
        for open_quote in open_quotes:
            open_quote.crossed_streak = 0
        return []

    fills: list[Fill] = []
    fill_timestamp = snapshot.timestamp
    required_streak = max(1, fill_latency_ticks) if fill_mode == FILL_MODE_LATENCY_PROB else 1
    probability = min(1.0, max(0.0, fill_probability))

    for open_quote in open_quotes:
        quote = open_quote.quote
        crossed = False

        if quote.side == QuoteSide.BID and external_ask <= quote.price:
            crossed = True
        elif quote.side == QuoteSide.ASK and external_bid >= quote.price:
            crossed = True

        since = open_quote.last_evaluated_at or quote.timestamp
        open_quote.last_evaluated_at = fill_timestamp

        if not crossed:
            open_quote.crossed_streak = 0
            if fill_mode != FILL_MODE_CALIBRATED or calibration is None:
                continue
            # specs/006 D1, FR-002: the price may have touched the quote and come back
            # between two snapshots. Chance = 1 - exp(-rate(distance) * dt).
            if quote.side == QuoteSide.BID:
                distance_bps = (external_ask - quote.price) / quote.price * 10_000
            else:
                distance_bps = (quote.price - external_bid) / quote.price * 10_000
            dt_sec = (fill_timestamp - since).total_seconds()
            # specs/007 D3: scaled by recent volatility when the calibration has k.
            chance = calibration.fill_chance(distance_bps, dt_sec, volatility)
            # Keyed on the quote's content, not its id: the paper broker's ids are random,
            # and the same inputs must give the same fills (FR-003).
            draw_key = f"{quote.side.value}:{quote.price!r}:{quote.timestamp.isoformat()}"
            if chance <= 0 or _deterministic_unit_random(draw_key, tick_index) >= chance:
                continue
            fills.append(_fill(open_quote, quote.size, maker_fee_bps, fill_timestamp))
            continue

        open_quote.crossed_streak += 1

        if fill_mode == FILL_MODE_LATENCY_PROB:
            if open_quote.crossed_streak < required_streak:
                continue
            if _deterministic_unit_random(open_quote.quote_id, tick_index) > probability:
                continue
            size_mode: FillMode = FILL_MODE_PARTIAL
        elif fill_mode == FILL_MODE_CALIBRATED:
            size_mode = FILL_MODE_FULL_CROSS
        else:
            size_mode = fill_mode

        fill_size = _fill_size_for_cross(quote, snapshot, size_mode)
        if fill_size is None or fill_size <= 0:
            continue

        fills.append(_fill(open_quote, fill_size, maker_fee_bps, fill_timestamp))

    return fills


def _fill(open_quote: OpenQuote, size: float, maker_fee_bps: float, at: datetime) -> Fill:
    quote = open_quote.quote
    return Fill(
        symbol=quote.symbol,
        side=quote.side,
        price=quote.price,
        size=size,
        fee=maker_fee(quote.price * size, maker_fee_bps),
        timestamp=at,
        quote_id=open_quote.quote_id,
    )
