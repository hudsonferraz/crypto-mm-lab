"""Paper shadow books: one per fill model, on the same testnet snapshots (specs/005 D3, R8).

A shadow never decides which quotes are sent; the real book does. Each shadow only
answers "would my fill model have filled this real order, and how much?", using the
same ``detect_fills`` code as the main paper broker. It needs no balance check.

Order window: a real order is evaluated at every tick whose snapshot is taken after the
order was acknowledged. An order the lab cancelled stops being evaluated once the cancel
happened. An order that filled on the exchange is evaluated once more at the first
snapshot after its fill, so the paper model gets to see the book that filled it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.execution.fill_calibration import FillCalibration, VolatilityTracker
from app.execution.fill_model import FillMode, OpenQuote, detect_fills, snapshot_mid
from app.models.domain import OrderBookSnapshot, Quote, QuoteSide
from app.models.testnet import ShadowFill, TestnetOrder

_EPS = 1e-12


def in_window(order: TestnetOrder, snap_at: datetime, prev_snap_at: datetime | None) -> bool:
    if order.acked_at is None or order.acked_at > snap_at:
        return False
    if order.ended_at is None:
        return True
    if order.state == "filled":
        return prev_snap_at is None or order.ended_at > prev_snap_at
    return order.ended_at > snap_at


class ShadowBook:
    def __init__(
        self,
        fill_model: FillMode,
        *,
        maker_fee_bps: float,
        fill_latency_ticks: int,
        fill_probability: float,
        calibration: FillCalibration | None = None,
    ) -> None:
        self.fill_model = fill_model
        self._maker_fee_bps = maker_fee_bps
        self._latency = fill_latency_ticks
        self._probability = fill_probability
        self._calibration = calibration
        self._volatility = VolatilityTracker()  # specs/007 D2
        self._quotes: dict[str, OpenQuote] = {}
        self.filled: dict[str, float] = {}

    def evaluate(
        self,
        snapshot: OrderBookSnapshot,
        orders: list[TestnetOrder],
        *,
        tick_seq: int,
        session_id: str,
    ) -> list[ShadowFill]:
        # Every snapshot feeds the volatility measure, even one with no order to evaluate,
        # so replay and the live shadow see the same history (specs/007 FR-002).
        volatility = self._volatility.update(snapshot.timestamp, snapshot_mid(snapshot))
        live_ids = {order.client_order_id for order in orders}
        self._quotes = {cid: q for cid, q in self._quotes.items() if cid in live_ids}
        batch: list[OpenQuote] = []
        for order in orders:
            open_quote = self._quotes.get(order.client_order_id)
            if open_quote is None:
                remaining = order.size - self.filled.get(order.client_order_id, 0.0)
                if remaining <= _EPS:
                    continue
                open_quote = OpenQuote(
                    quote_id=order.client_order_id,
                    quote=Quote(
                        symbol=snapshot.symbol,
                        side=QuoteSide.BID if order.side == "bid" else QuoteSide.ASK,
                        price=order.price,
                        size=remaining,
                        timestamp=order.acked_at or snapshot.timestamp,
                        quote_id=order.client_order_id,
                    ),
                )
                self._quotes[order.client_order_id] = open_quote
            batch.append(open_quote)
        if not batch:
            return []
        fills = detect_fills(
            batch,
            snapshot,
            self._maker_fee_bps,
            fill_mode=self.fill_model,
            fill_latency_ticks=self._latency,
            fill_probability=self._probability,
            tick_index=tick_seq,
            calibration=self._calibration,
            volatility=volatility,
        )
        out: list[ShadowFill] = []
        for fill in fills:
            cid = fill.quote_id
            open_quote = self._quotes[cid]
            remaining = open_quote.quote.size - fill.size
            self.filled[cid] = self.filled.get(cid, 0.0) + fill.size
            if remaining > _EPS:
                self._quotes[cid] = OpenQuote(
                    quote_id=cid,
                    quote=replace(open_quote.quote, size=remaining),
                    crossed_streak=0,
                    last_evaluated_at=snapshot.timestamp,
                )
            else:
                del self._quotes[cid]
            out.append(
                ShadowFill(
                    fill_model=self.fill_model,
                    client_order_id=cid,
                    session_id=session_id,
                    side="bid" if fill.side == QuoteSide.BID else "ask",
                    price=fill.price,
                    size=fill.size,
                    fee=fill.fee,
                    at=snapshot.timestamp,
                    tick_seq=tick_seq,
                )
            )
        return out
