"""Replay recorded testnet sessions through the paper fill models (specs/006 D6, research R4, R5).

A recording keeps every tick's best bid/ask and the exact list of orders the live shadow
books evaluated on it. Rebuilding a one-level snapshot from each tick and running the
same ShadowBook code therefore reproduces the live shadow fills exactly (FR-009), and
lets fill models added after the recording was made be compared on it.

The same walk over ticks yields the calibration intervals: one per order and tick, from
the order's previous evaluation to the tick, labeled by whether the order's first real
fill fell inside it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.execution.fill_calibration import (
    MAX_INTERVAL_SEC,
    FillCalibration,
    VolatilityTracker,
    calibration_from_params,
)
from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.models.testnet import FILL_MODELS, ShadowFill, TickRecord
from app.services.testnet.recorded import RecordedSession
from app.services.testnet.shadow import ShadowBook

CALIBRATED_MODEL = "calibrated_fill"
# What a recording holds when it predates notes.shadow_models (specs/006 data-model).
LEGACY_SHADOW_MODELS: tuple[str, ...] = ("full_cross_fill", "partial_fill", "latency_prob_fill")


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One look at one order on one recorded tick."""

    client_order_id: str
    tick_seq: int
    at: datetime
    crossed: bool
    distance_bps: float  # to the other side's best price; <= 0 when crossed
    dt_sec: float  # since the previous look (or the acknowledgement), capped
    event: bool | None  # first real fill inside this interval; None after the first fill
    volatility: float | None = None  # bps per sqrt(s) at this look (specs/007 D2)


@dataclass(frozen=True, slots=True)
class CalibrationInterval:
    session_id: str
    client_order_id: str
    dt_sec: float
    distance_bps: float
    event: bool
    volatility: float | None = None


def stored_models(recording: RecordedSession) -> tuple[str, ...]:
    models = recording.notes.get("shadow_models") if recording.notes else None
    return tuple(models) if models else LEGACY_SHADOW_MODELS


def tick_snapshot(tick: TickRecord, symbol: str) -> OrderBookSnapshot:
    bids = (
        (OrderBookLevel(tick.best_bid, tick.bid_size or 0.0),) if tick.best_bid is not None else ()
    )
    asks = (
        (OrderBookLevel(tick.best_ask, tick.ask_size or 0.0),) if tick.best_ask is not None else ()
    )
    return OrderBookSnapshot(symbol=symbol, bids=bids, asks=asks, timestamp=tick.at)


def replay_shadows(
    recording: RecordedSession,
    models: tuple[str, ...] = FILL_MODELS,
    calibration: FillCalibration | None = None,
) -> list[ShadowFill]:
    session = recording.session
    settings = session.settings or {}
    symbol = settings.get("symbol", "BTCUSDT")
    books = [
        ShadowBook(
            model,  # type: ignore[arg-type]
            maker_fee_bps=session.maker_commission_bps,
            fill_latency_ticks=int(settings.get("fill_latency_ticks", 2)),
            fill_probability=float(settings.get("fill_probability", 0.7)),
            calibration=calibration if model == CALIBRATED_MODEL else None,
        )
        for model in models
    ]
    orders = {o.client_order_id: o for o in recording.orders}
    out: list[ShadowFill] = []
    for tick in sorted(recording.ticks, key=lambda t: t.seq):
        snapshot = tick_snapshot(tick, symbol)
        window = [orders[cid] for cid in tick.open_orders if cid in orders]
        for book in books:
            out.extend(
                book.evaluate(snapshot, window, tick_seq=tick.seq, session_id=session.session_id)
            )
    return out


def _fill_key(fill: ShadowFill) -> tuple:
    return (fill.fill_model, fill.client_order_id, fill.tick_seq, round(fill.size, 12), fill.price)


_UNKNOWN = object()


def used_calibration(recording: RecordedSession, known: dict | None = None):
    """The calibration the recording's calibrated_fill shadow used (specs/007 D6, FR-010).

    From the recording's own notes when the recorder stored it, else from ``known``
    (session id to parameters, for recordings made before that), else ``_UNKNOWN``.
    """
    notes = recording.notes or {}
    if "calibration" in notes:
        params = notes["calibration"]
    elif known is not None and recording.session.session_id in known:
        params = known[recording.session.session_id]
    else:
        return _UNKNOWN
    return calibration_from_params(params) if params else None


def checked_models(recording: RecordedSession, known: dict | None = None) -> tuple[str, ...]:
    """The stored models replay can verify: calibrated_fill needs its calibration."""
    models = stored_models(recording)
    if CALIBRATED_MODEL in models and used_calibration(recording, known) is _UNKNOWN:
        return tuple(m for m in models if m != CALIBRATED_MODEL)
    return models


def replay_mismatches(recording: RecordedSession, known: dict | None = None) -> list[str]:
    """Differences between stored and replayed shadow fills for the checked models."""
    models = checked_models(recording, known)
    calibration = used_calibration(recording, known)
    if calibration is _UNKNOWN:
        calibration = None
    stored = sorted(_fill_key(f) for f in recording.shadow_fills if f.fill_model in models)
    replayed = sorted(_fill_key(f) for f in replay_shadows(recording, models, calibration))
    if stored == replayed:
        return []
    missing = sorted(set(stored) - set(replayed))
    extra = sorted(set(replayed) - set(stored))
    return [f"stored only: {k}" for k in missing] + [f"replayed only: {k}" for k in extra]


def evaluations(recording: RecordedSession) -> dict[str, list[Evaluation]]:
    """Every look the shadow books took at every order, in tick order (research R4)."""
    orders = {o.client_order_id: o for o in recording.orders}
    first_fill: dict[str, datetime] = {}
    for fill in recording.fills:
        if fill.client_order_id not in first_fill or fill.at < first_fill[fill.client_order_id]:
            first_fill[fill.client_order_id] = fill.at
    last_look: dict[str, datetime] = {}
    out: dict[str, list[Evaluation]] = {}
    tracker = VolatilityTracker()
    for tick in sorted(recording.ticks, key=lambda t: t.seq):
        # Fed on every tick exactly as each ShadowBook feeds its own (specs/007 FR-002).
        both = tick.best_bid is not None and tick.best_ask is not None
        volatility = tracker.update(tick.at, (tick.best_bid + tick.best_ask) / 2 if both else None)
        if not both:
            continue  # the models skip such a tick and keep the interval open
        for cid in tick.open_orders:
            order = orders.get(cid)
            if order is None or order.acked_at is None:
                continue
            since = last_look.get(cid, order.acked_at)
            last_look[cid] = tick.at
            if order.side == "bid":
                distance = (tick.best_ask - order.price) / order.price * 10_000
            else:
                distance = (order.price - tick.best_bid) / order.price * 10_000
            crossed = distance <= 0
            dt = min(MAX_INTERVAL_SEC, max(0.0, (tick.at - since).total_seconds()))
            fill_at = first_fill.get(cid)
            if fill_at is not None and fill_at <= since:
                event: bool | None = None
            else:
                event = fill_at is not None and fill_at <= tick.at
            out.setdefault(cid, []).append(
                Evaluation(cid, tick.seq, tick.at, crossed, distance, dt, event, volatility)
            )
    return out


def calibration_intervals(recording: RecordedSession) -> list[CalibrationInterval]:
    """Uncrossed looks before the first real fill: the data the fit uses (FR-005)."""
    sid = recording.session.session_id
    out = []
    for cid, looks in sorted(evaluations(recording).items()):
        for look in looks:
            if look.crossed or look.event is None:
                continue
            out.append(
                CalibrationInterval(
                    sid, cid, look.dt_sec, look.distance_bps, look.event, look.volatility
                )
            )
    return out


def complete_recording(
    recording: RecordedSession, calibration: FillCalibration | None
) -> RecordedSession:
    """The recording with shadow fills for every current model, replaying the missing ones.

    Recordings made before a model existed hold no shadow fills for it; FR-012 asks the
    keyless comparison to show it anyway, computed from the recorded ticks.
    """
    stored = stored_models(recording)
    missing = tuple(m for m in FILL_MODELS if m not in stored)
    if not missing:
        return recording
    extra = replay_shadows(recording, missing, calibration)
    notes = {
        **(recording.notes or {}),
        "shadow_models": [*stored, *missing],
        "replayed_models": list(missing),
    }
    return replace(recording, shadow_fills=[*recording.shadow_fills, *extra], notes=notes)
