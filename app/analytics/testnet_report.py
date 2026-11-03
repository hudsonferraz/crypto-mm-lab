"""Serialization, payloads and plain-language explanations for the testnet trader (specs/005).

Nothing here ever sees a key: payloads are built from records and status only.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from app.models.testnet import (
    FillComparison,
    OrderEvent,
    ReconcileState,
    ShadowFill,
    TestnetFill,
    TestnetOrder,
    TestnetSession,
    TickRecord,
    TraderStatus,
)

EXPLANATIONS: dict[str, str] = {
    "D1": (
        "Orders only ever go to the Binance spot testnet, which uses fake money. The address "
        "is fixed in the lab's code, and the trader refuses to talk to any other host, so a "
        "real-exchange key placed in the wrong setting cannot trade."
    ),
    "D2": (
        "Without testnet keys the trader stays off and the rest of the lab runs as before. "
        'This page then shows a recorded real testnet session, labeled "recorded". Live and '
        "recorded numbers are never mixed."
    ),
    "D3": (
        "The testnet trader is its own book: it quotes from the testnet order book, not the "
        "mainnet book of the main paper loop. Three paper books, one per fill model, see the "
        "same testnet snapshots and the same orders, so paper and real are compared on "
        "identical inputs."
    ),
    "D4": (
        "Every quote is a post-only order: the exchange rejects it instead of letting it trade "
        "immediately, because a market maker's quote must only add liquidity, as the paper "
        "model assumes. Rejections are counted, never resent as taking orders. The kill switch "
        "cancels every order and checks with the exchange that none are left."
    ),
    "D5": (
        "The headline is how well each paper fill model matches the real matching engine, not "
        "testnet profit. Testnet order flow is thin and mostly bots copying mainnet prices, so "
        "its PnL says little about mainnet."
    ),
    "D6": (
        "Every order is tagged and checked against the exchange: on every cycle, every 30 "
        "seconds in full, and after any failed request. Orders left over from an earlier run "
        "are cancelled at start, and any tagged order the lab cannot explain is reported and "
        "cancelled."
    ),
    "D7": (
        "The trader runs beside the main loop, never inside it, and stays well under the "
        "testnet's request limits, slowing down when the exchange says usage is high."
    ),
}

FIELD_EXPLANATIONS: dict[str, str] = {
    "fill_rate": (
        "Share of the orders sent that got any fill: on the real testnet, and according to the "
        "paper model on the same snapshots."
    ),
    "matched": "Orders both the real engine and the paper model filled.",
    "paper_only": (
        "The paper model filled them, the real engine did not: the model is too optimistic "
        "for these."
    ),
    "real_only": (
        "The real engine filled them, the paper model did not: someone traded into our price "
        "without the book visibly crossing it."
    ),
    "time_to_fill": "Median seconds from the exchange accepting the order to its first fill.",
    "size_price": (
        "For matched orders, paper minus real filled size, and the fill price difference in "
        "basis points."
    ),
    "pnl_gap": (
        "Paper PnL minus real PnL on the same orders, marking inventory at the last testnet "
        "mid. Testnet PnL is not a mainnet forecast (D5)."
    ),
    "rejects": (
        "Orders the exchange refused: post-only crossings, size or value below the minimum, "
        "or not enough testnet balance (D4)."
    ),
    "reconcile": (
        "Result of the last full check of the lab's order records against the exchange (D6)."
    ),
}

HOW_TO_ENABLE = (
    "Create a free key at testnet.binance.vision (GitHub login), then set "
    "BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET in .env and restart the lab."
)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# --- records <-> dicts (storage and the recorded session) ----------------------------


def order_to_dict(order: TestnetOrder) -> dict:
    return {
        "client_order_id": order.client_order_id,
        "session_id": order.session_id,
        "side": order.side,
        "price": order.price,
        "size": order.size,
        "placed_at": _dt(order.placed_at),
        "quote_id": order.quote_id,
        "exchange_order_id": order.exchange_order_id,
        "state": order.state,
        "filled_size": order.filled_size,
        "filled_notional": order.filled_notional,
        "reject_reason": order.reject_reason,
        "acked_at": _dt(order.acked_at),
        "ended_at": _dt(order.ended_at),
        "events": [{"at": _dt(e.at), "kind": e.kind, "detail": e.detail} for e in order.events],
    }


def order_from_dict(data: dict) -> TestnetOrder:
    return TestnetOrder(
        client_order_id=data["client_order_id"],
        session_id=data["session_id"],
        side=data["side"],
        price=data["price"],
        size=data["size"],
        placed_at=_parse(data["placed_at"]),
        quote_id=data.get("quote_id"),
        exchange_order_id=data.get("exchange_order_id"),
        state=data["state"],
        filled_size=data.get("filled_size", 0.0),
        filled_notional=data.get("filled_notional", 0.0),
        reject_reason=data.get("reject_reason"),
        acked_at=_parse(data.get("acked_at")),
        ended_at=_parse(data.get("ended_at")),
        events=[
            OrderEvent(at=_parse(e["at"]), kind=e["kind"], detail=e.get("detail"))
            for e in data.get("events", [])
        ],
    )


def fill_to_dict(fill: TestnetFill) -> dict:
    data = asdict(fill)
    data["at"] = _dt(fill.at)
    return data


def fill_from_dict(data: dict) -> TestnetFill:
    return TestnetFill(**{**data, "at": _parse(data["at"])})


def shadow_to_dict(fill: ShadowFill) -> dict:
    data = asdict(fill)
    data["at"] = _dt(fill.at)
    return data


def shadow_from_dict(data: dict) -> ShadowFill:
    return ShadowFill(**{**data, "at": _parse(data["at"])})


def tick_to_dict(tick: TickRecord) -> dict:
    data = asdict(tick)
    data["at"] = _dt(tick.at)
    data["open_orders"] = list(tick.open_orders)
    return data


def tick_from_dict(data: dict) -> TickRecord:
    return TickRecord(
        **{**data, "at": _parse(data["at"]), "open_orders": tuple(data.get("open_orders", ()))}
    )


def session_to_dict(session: TestnetSession) -> dict:
    data = asdict(session)
    data["started_at"] = _dt(session.started_at)
    data["ended_at"] = _dt(session.ended_at)
    return data


def session_from_dict(data: dict) -> TestnetSession:
    return TestnetSession(
        **{
            **data,
            "started_at": _parse(data["started_at"]),
            "ended_at": _parse(data.get("ended_at")),
        }
    )


# --- API payloads ---------------------------------------------------------------------


def status_payload(status: TraderStatus) -> dict:
    reconcile: ReconcileState = status.reconcile
    return {
        "state": status.state,
        "reason": status.reason,
        "keys_present": status.keys_present,
        "session_id": status.session_id,
        "started_at": _dt(status.started_at),
        "last_iteration_at": _dt(status.last_iteration_at),
        "open_orders": status.open_orders,
        "kill_switch_active": status.kill_switch_active,
        "weight_used_ratio": status.weight_used_ratio,
        "counters": dict(status.counters),
        "reconcile": {
            "last_at": _dt(reconcile.last_at),
            "ok": reconcile.ok,
            "unexplained": reconcile.unexplained,
        },
    }


def order_payload(order: TestnetOrder, paper: dict[str, float] | None = None) -> dict:
    return {
        "client_order_id": order.client_order_id,
        "side": order.side,
        "price": order.price,
        "size": order.size,
        "state": order.state,
        "filled_size": order.filled_size,
        "avg_fill_price": order.avg_fill_price,
        "reject_reason": order.reject_reason,
        "placed_at": _dt(order.placed_at),
        "acked_at": _dt(order.acked_at),
        "ended_at": _dt(order.ended_at),
        "events": [{"at": _dt(e.at), "kind": e.kind, "detail": e.detail} for e in order.events],
        "paper": paper or {},
    }


def fill_payload(fill: TestnetFill) -> dict:
    return {
        "trade_id": fill.trade_id,
        "client_order_id": fill.client_order_id,
        "side": fill.side,
        "price": fill.price,
        "size": fill.size,
        "fee": fill.fee,
        "fee_asset": fill.fee_asset,
        "at": _dt(fill.at),
    }


def comparison_payload(item: FillComparison) -> dict:
    return asdict(item)


def session_payload(session: TestnetSession, orders: int, fills: int) -> dict:
    return {
        "session_id": session.session_id,
        "source": session.source,
        "started_at": _dt(session.started_at),
        "ended_at": _dt(session.ended_at),
        "end_reason": session.end_reason,
        "orders": orders,
        "fills": fills,
    }


def reject_counts(orders: list[TestnetOrder]) -> dict[str, int]:
    counts = {"post_only": 0, "min_size": 0, "min_notional": 0, "unfundable": 0, "other": 0}
    for order in orders:
        if order.state == "rejected" and order.reject_reason in counts:
            counts[order.reject_reason] += 1
    return counts
