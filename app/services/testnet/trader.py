"""Binance spot testnet trader (specs/005-binance-testnet-execution).

Runs the configured market-making strategy on the testnet with real post-only orders,
beside the main tick loop and never inside it (D7). Every order is tagged, tracked
through its life cycle and reconciled with the exchange (D6). Paper shadow books
evaluate the same orders on the same testnet snapshots (D3).

Public steps (``startup``, ``iterate``, ``reconcile``, ``shutdown``) are what the
background loop calls; tests drive them directly against a fake exchange.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import structlog

from app.adapters.cex.binance_testnet import (
    AuthFailed,
    OrderRejected,
    RateLimited,
    TestnetClient,
    TestnetError,
    TestnetUnavailable,
    UnknownOrder,
)
from app.analytics.inventory import InventoryTracker
from app.config.settings import Settings
from app.execution.fill_calibration import load_fill_calibration
from app.market_data.normalizer import normalize_ccxt_orderbook
from app.market_data.orderbook import mid_price
from app.models.domain import Fill, OrderBookSnapshot, Quote, QuoteSide
from app.models.testnet import (
    COUNTER_NAMES,
    FILL_MODELS,
    OPEN_ORDER_STATES,
    TAG_PREFIX,
    ReconcileState,
    ShadowFill,
    SymbolRules,
    TestnetFill,
    TestnetOrder,
    TestnetSession,
    TickRecord,
    TraderState,
    TraderStatus,
)
from app.observability import metrics as prom
from app.risk.kill_switch import KillSwitch
from app.risk.limits import filter_quotes_by_position_limit
from app.services.testnet.comparison import compare
from app.services.testnet.rules import (
    check_minimums,
    format_step,
    round_price,
    round_size,
    rules_from_exchange_info,
)
from app.services.testnet.shadow import ShadowBook, in_window
from app.storage.repository import Repository
from app.strategies.factory import build_strategy

logger = structlog.get_logger(__name__)

_EPS = 1e-12
_KILL_POLL_SEC = 0.2
_MAX_BACKOFF_SEC = 30.0
_WEIGHT_SLOWDOWN_RATIO = 0.7
_CANCEL_CONFIRM_ATTEMPTS = 5
_STATUS_FINAL = {
    "FILLED": "filled",
    "CANCELED": "cancelled",
    "EXPIRED": "expired",
    "EXPIRED_IN_MATCH": "expired",
    "REJECTED": "rejected",
}


class _SessionReset(Exception):
    """Raised after a testnet reset started a new session; the current step stops."""


def disabled_status(settings: Settings) -> TraderStatus:
    """Status when no trader runs (disabled or no keys); never reveals a key (FR-003)."""
    if not settings.testnet_enabled:
        return TraderStatus(
            state="disabled", reason="TESTNET_ENABLED=false", keys_present=False, source="none"
        )
    return TraderStatus(
        state="no_keys", reason="no testnet keys", keys_present=False, source="none"
    )


def _ms(value) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _session_of(client_order_id: str) -> str | None:
    if not client_order_id.startswith(TAG_PREFIX):
        return None
    rest = client_order_id[len(TAG_PREFIX) :]
    return rest.split("-", 1)[0] if "-" in rest else None


class TestnetTrader:
    __test__ = False

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        kill_switch: KillSwitch,
        *,
        client: TestnetClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._kill_switch = kill_switch
        self._clock = clock or (lambda: datetime.now(UTC))
        if client is None:
            client = TestnetClient(
                settings.binance_testnet_api_key.get_secret_value(),
                settings.binance_testnet_api_secret.get_secret_value(),
                timeout_sec=settings.testnet_timeout_sec,
            )
        self._client = client
        self._symbol_pair = settings.symbol
        self._symbol = settings.symbol.replace("/", "").upper()
        self._base_asset, _, self._quote_asset = settings.symbol.upper().partition("/")
        self._strategy = build_strategy(
            settings.model_copy(
                update={
                    "quote_spread_bps": settings.testnet_spread_bps,
                    "quote_size": settings.testnet_size,
                }
            )
        )
        self._metrics = settings.metrics_enabled

        self._state: TraderState = "starting"
        self._reason: str | None = None
        self._rules: SymbolRules | None = None
        self._session: TestnetSession | None = None
        self._orders: dict[str, TestnetOrder] = {}
        self._by_exchange_id: dict[int, str] = {}
        self._seq = 0
        self._tick_seq = 0
        self._last_trade_id: int | None = None
        self._first_trade_id: int | None = None
        self._inventory: InventoryTracker | None = None
        self._shadows: list[ShadowBook] = []
        self._last_snapshot_at: datetime | None = None
        self._last_mid: float | None = None
        self._account_free: dict[str, float] = {}
        self._account_total: dict[str, float] = {}
        self._maker_bps = 0.0
        self._counters = dict.fromkeys(COUNTER_NAMES, 0)
        self._reconcile = ReconcileState()
        self._last_reconcile_mono: float | None = None
        self._need_reconcile = False
        self._started_at: datetime | None = None
        self._last_iteration_at: datetime | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._dirty: dict[str, TestnetOrder] = {}

    # --- status ----------------------------------------------------------------

    @property
    def status(self) -> TraderStatus:
        return TraderStatus(
            state=self._state,
            reason=self._reason,
            keys_present=True,
            source="live",
            session_id=self._session.session_id if self._session else None,
            started_at=self._started_at,
            last_iteration_at=self._last_iteration_at,
            open_orders=len(self.open_orders),
            kill_switch_active=self._kill_switch.active,
            weight_used_ratio=self._client.weight_used_ratio,
            counters=dict(self._counters),
            reconcile=self._reconcile,
        )

    @property
    def session(self) -> TestnetSession | None:
        return self._session

    @property
    def open_orders(self) -> list[TestnetOrder]:
        return [o for o in self._orders.values() if o.state in OPEN_ORDER_STATES]

    @property
    def last_mid(self) -> float | None:
        return self._last_mid

    @property
    def account_totals(self) -> dict[str, float]:
        return dict(self._account_total)

    @property
    def virtual_balances(self) -> tuple[float, float]:
        inv = self._inventory
        return (inv.base_amount, inv.quote_amount) if inv is not None else (0.0, 0.0)

    def _set_state(self, state: TraderState, reason: str | None = None) -> None:
        if state != self._state or reason != self._reason:
            logger.info("testnet_trader_state", state=state, reason=reason)
        self._state = state
        self._reason = reason
        if self._metrics:
            prom.TESTNET_STATE.set(1 if state in ("running", "paused") else 0)

    def _count(self, name: str, amount: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + amount
        if self._metrics:
            prom.TESTNET_EVENTS.labels(event=name).inc(amount)

    # --- background loop ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="testnet-trader")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=20)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        await self._client.aclose()

    async def _run(self) -> None:
        backoff = self._settings.testnet_poll_interval_sec
        try:
            while not self._stopping:
                try:
                    if self._session is None:
                        await self.startup()
                    await self.iterate()
                    backoff = self._settings.testnet_poll_interval_sec
                    await self._wait(self._interval())
                except AuthFailed:
                    return  # state already auth_failed; never retried
                except RateLimited as exc:
                    self._set_state(
                        "rate_limited", f"exchange asked to wait {exc.retry_after:.0f} s"
                    )
                    await self._wait(exc.retry_after)
                except (TestnetError, OSError) as exc:
                    self._count("errors")
                    self._need_reconcile = True
                    self._set_state("unavailable", str(exc))
                    await self._wait(backoff)
                    backoff = min(_MAX_BACKOFF_SEC, backoff * 2)
                except Exception as exc:  # keep the trader alive; the loop is unaffected
                    self._count("errors")
                    logger.exception("testnet_trader_iteration_failed")
                    self._set_state("unavailable", f"internal error: {type(exc).__name__}")
                    await self._wait(backoff)
                    backoff = min(_MAX_BACKOFF_SEC, backoff * 2)
        finally:
            await self.shutdown()

    def _interval(self) -> float:
        interval = self._settings.testnet_poll_interval_sec
        ratio = self._client.weight_used_ratio
        if ratio is not None and ratio > _WEIGHT_SLOWDOWN_RATIO:
            interval *= 2  # D7: slow down when the exchange reports high usage
        return interval

    async def _wait(self, seconds: float) -> None:
        """Sleep, but wake early when the kill switch flips on or the trader stops (SC-002)."""
        deadline = time.monotonic() + seconds
        paused = self._state in ("paused", "cancel_pending")
        while not self._stopping:
            if self._kill_switch.active and not paused and self._session is not None:
                return
            if not self._kill_switch.active and paused:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(_KILL_POLL_SEC, remaining))

    # --- startup -------------------------------------------------------------------

    async def startup(self) -> None:
        self._set_state("starting")
        try:
            await self._client.sync_time()
            self._rules = rules_from_exchange_info(
                await self._client.exchange_info(self._symbol), self._symbol
            )
            account = await self._client.account()
        except AuthFailed:
            self._set_state("auth_failed", "authentication failed: check the testnet keys")
            raise
        self._read_account(account)
        await self._close_previous_sessions()
        await self._cancel_leftovers()
        await self._open_session()
        self._started_at = self._started_at or self._clock()
        self._last_reconcile_mono = time.monotonic()
        self._reconcile = ReconcileState(last_at=self._clock(), ok=True, unexplained=0)
        self._set_state("running")

    def _read_account(self, account: dict) -> None:
        free, total = {}, {}
        for item in account.get("balances", []):
            asset = item.get("asset")
            f, locked = float(item.get("free", 0)), float(item.get("locked", 0))
            free[asset], total[asset] = f, f + locked
        self._account_free, self._account_total = free, total
        rates = account.get("commissionRates") or {}
        if "maker" in rates:
            self._maker_bps = float(rates["maker"]) * 10_000
        elif "makerCommission" in account:
            self._maker_bps = float(account["makerCommission"])  # in 0.01 %, i.e. bps

    async def _close_previous_sessions(self) -> None:
        now = self._clock()
        for session in await asyncio.to_thread(self._repository.get_testnet_sessions, 50):
            if session.source == "live" and session.ended_at is None:
                session.ended_at, session.end_reason = now, "restart"
                await asyncio.to_thread(self._repository.save_testnet_session, session)

    async def _cancel_leftovers(self) -> None:
        """FR-013: tagged orders still open from an earlier run are cancelled at start."""
        for entry in await self._client.open_orders(self._symbol):
            cid = entry.get("clientOrderId", "")
            if not cid.startswith(TAG_PREFIX):
                continue  # someone else's order: never touched
            try:
                await self._client.cancel_order(self._symbol, cid)
            except UnknownOrder:
                continue
            self._count("leftovers_cancelled")
            now = self._clock()
            record = await asyncio.to_thread(self._repository.get_testnet_order, cid)
            if record is not None:
                record.state = "cancelled"
                record.ended_at = now
                record.add_event(now, "leftover_cancelled")
                await asyncio.to_thread(self._repository.upsert_testnet_orders, [record])

    async def _open_session(self) -> None:
        now = self._clock()
        recent = await self._client.my_trades(self._symbol, limit=1)
        self._last_trade_id = max((int(t["id"]) for t in recent), default=None)
        self._first_trade_id = None
        session = TestnetSession(
            session_id=secrets.token_hex(3),
            source="live",
            started_at=now,
            start_base=self._settings.testnet_initial_base,
            start_quote=self._settings.initial_quote_balance,
            maker_commission_bps=self._maker_bps,
            account_base=self._account_total.get(self._base_asset),
            account_quote=self._account_total.get(self._quote_asset),
            settings={
                "symbol": self._symbol,
                "strategy": self._settings.strategy,
                "spread_bps": self._settings.testnet_spread_bps,
                "quote_size": self._settings.testnet_size,
                "replace_threshold_bps": self._settings.testnet_replace_threshold_bps,
                "poll_interval_sec": self._settings.testnet_poll_interval_sec,
                "fill_latency_ticks": self._settings.fill_latency_ticks,
                "fill_probability": self._settings.fill_probability,
            },
        )
        self._session = session
        self._orders, self._by_exchange_id, self._dirty = {}, {}, {}
        self._seq = self._tick_seq = 0
        self._last_snapshot_at = None
        inventory = InventoryTracker(self._symbol_pair, session.start_quote)
        inventory.restore_state(
            base_amount=session.start_base,
            quote_amount=session.start_quote,
            average_entry_price=0.0,
            realized_pnl=0.0,
            total_fees=0.0,
            last_updated=None,
        )
        self._inventory = inventory
        calibration = load_fill_calibration(self._settings.fill_calibration_path)
        self._shadows = [
            ShadowBook(
                model,
                maker_fee_bps=self._maker_bps,
                fill_latency_ticks=self._settings.fill_latency_ticks,
                fill_probability=self._settings.fill_probability,
                calibration=calibration if model == "calibrated_fill" else None,
            )
            for model in FILL_MODELS
        ]
        await asyncio.to_thread(self._repository.save_testnet_session, session)

    # --- one iteration -------------------------------------------------------------

    async def iterate(self) -> None:
        if self._session is None:
            await self.startup()
        try:
            await self._iterate()
        except _SessionReset:
            await self._flush()
            self._last_iteration_at = self._clock()

    async def _iterate(self) -> None:
        now = self._clock()
        if self._kill_switch.active or self._stopping:
            cleared = await self.cancel_all()
            if self._kill_switch.active:
                self._set_state("paused" if cleared else "cancel_pending", "kill switch on")
            await self._flush()
            self._last_iteration_at = now
            return

        raw = await self._client.depth(self._symbol, 20)
        snapshot = normalize_ccxt_orderbook(self._symbol_pair, {**raw, "timestamp": None})
        snapshot = replace(snapshot, timestamp=self._clock())
        await self._sync_orders()
        tick, fills = self._evaluate_shadows(snapshot)
        await asyncio.to_thread(self._repository.save_testnet_tick, tick)
        await self._quote(snapshot)
        if (
            self._need_reconcile
            or self._last_reconcile_mono is None
            or time.monotonic() - self._last_reconcile_mono
            >= self._settings.testnet_reconcile_interval_sec
        ):
            await self.reconcile()
        await asyncio.to_thread(self._repository.save_testnet_shadow_fills, fills)
        await self._flush()
        self._last_iteration_at = self._clock()
        if self._state != "running":
            self._set_state("running")
        self._record_metrics()

    def _mark(self, order: TestnetOrder) -> None:
        self._dirty[order.client_order_id] = order

    async def _flush(self) -> None:
        if self._dirty:
            batch = list(self._dirty.values())
            self._dirty = {}
            await asyncio.to_thread(self._repository.upsert_testnet_orders, batch)

    # --- fills and order states ------------------------------------------------------

    async def _read_fills(self) -> None:
        from_id = self._last_trade_id + 1 if self._last_trade_id is not None else None
        trades = await self._client.my_trades(self._symbol, from_id=from_id)
        new_fills: list[TestnetFill] = []
        for trade in sorted(trades, key=lambda t: int(t["id"])):
            trade_id = int(trade["id"])
            if self._last_trade_id is not None and trade_id <= self._last_trade_id:
                continue
            self._last_trade_id = trade_id
            cid = self._by_exchange_id.get(int(trade["orderId"]))
            if cid is None:
                continue  # not a lab order of this session
            order = self._orders[cid]
            fill = TestnetFill(
                trade_id=trade_id,
                client_order_id=cid,
                session_id=self._session.session_id,
                side=order.side,
                price=float(trade["price"]),
                size=float(trade["qty"]),
                fee=float(trade.get("commission", 0)),
                fee_asset=trade.get("commissionAsset", self._quote_asset),
                at=_ms(trade["time"]),
                is_maker=bool(trade.get("isMaker", True)),
            )
            if self._first_trade_id is None:
                self._first_trade_id = trade_id
            new_fills.append(fill)
            self._apply_fill(order, fill)
        await asyncio.to_thread(self._repository.save_testnet_fills, new_fills)

    async def _sync_orders(self) -> None:
        session = self._session
        await self._read_fills()
        exchange_open = {
            e["clientOrderId"]: e for e in await self._client.open_orders(self._symbol)
        }
        for cid, entry in exchange_open.items():
            if not cid.startswith(TAG_PREFIX) or cid in self._orders:
                continue
            # FR-013 / FR-014: a tagged order with no record of this session.
            unexplained = _session_of(cid) == session.session_id
            try:
                await self._client.cancel_order(self._symbol, cid)
            except UnknownOrder:
                continue
            self._count("unexplained" if unexplained else "leftovers_cancelled")
            logger.warning(
                "testnet_tagged_order_cancelled",
                client_order_id=cid,
                kind="unexplained" if unexplained else "leftover",
            )
        for order in list(self._orders.values()):
            if order.state not in OPEN_ORDER_STATES or order.state == "new":
                continue
            entry = exchange_open.get(order.client_order_id)
            if entry is not None:
                self._adopt(order, entry)
                continue
            await self._resolve_missing(order)

    def _apply_fill(self, order: TestnetOrder, fill: TestnetFill) -> None:
        order.filled_size += fill.size
        order.filled_notional += fill.size * fill.price
        order.add_event(fill.at, "fill", f"{fill.size:g} @ {fill.price:.2f}")
        if order.state in ("open", "unknown"):
            order.state = "partially_filled"
        if order.filled_size >= order.size - _EPS:
            order.state = "filled"
            order.ended_at = order.ended_at or fill.at
        fee_quote = (
            fill.fee
            if fill.fee_asset == self._quote_asset
            else (fill.fee * fill.price if fill.fee_asset == self._base_asset else 0.0)
        )
        self._inventory.apply_fill(
            Fill(
                symbol=self._symbol_pair,
                side=QuoteSide.BID if fill.side == "bid" else QuoteSide.ASK,
                price=fill.price,
                size=fill.size,
                fee=fee_quote,
                timestamp=fill.at,
                quote_id=order.quote_id,
            ),
            fill.at,
        )
        self._count("filled" if order.state == "filled" else "partially_filled")
        self._mark(order)

    def _adopt(self, order: TestnetOrder, entry: dict) -> None:
        """Align a record with the exchange's view of an open order."""
        changed = False
        if order.exchange_order_id is None and entry.get("orderId") is not None:
            order.exchange_order_id = int(entry["orderId"])
            self._by_exchange_id[order.exchange_order_id] = order.client_order_id
            changed = True
        if order.state == "unknown":
            order.state = "partially_filled" if float(entry.get("executedQty", 0)) > 0 else "open"
            if order.acked_at is None:
                order.acked_at = _ms(entry["time"]) if entry.get("time") else self._clock()
            order.add_event(self._clock(), "reconciled", "found open on the exchange")
            changed = True
        if changed:
            self._mark(order)

    async def _resolve_missing(self, order: TestnetOrder) -> None:
        """A recorded open order is no longer listed: ask the exchange what happened."""
        try:
            entry = await self._client.get_order(self._symbol, order.client_order_id)
        except UnknownOrder:
            now = self._clock()
            if order.state == "unknown" and order.acked_at is None:
                order.state, order.reject_reason, order.ended_at = "rejected", "other", now
                order.add_event(now, "reconciled", "never reached the exchange")
                self._mark(order)
                return
            await self._testnet_reset("a recorded order no longer exists on the exchange")
            return
        self._apply_exchange_state(order, entry)

    def _apply_exchange_state(self, order: TestnetOrder, entry: dict) -> None:
        status = entry.get("status", "")
        if order.exchange_order_id is None and entry.get("orderId") is not None:
            order.exchange_order_id = int(entry["orderId"])
            self._by_exchange_id[order.exchange_order_id] = order.client_order_id
        if order.acked_at is None:
            order.acked_at = _ms(entry["time"]) if entry.get("time") else self._clock()
        final = _STATUS_FINAL.get(status)
        if final is None:
            if order.state == "unknown":
                order.state = "open"
                order.add_event(self._clock(), "reconciled", f"exchange status {status}")
                self._mark(order)
            return
        if final == "filled" and order.filled_size < order.size - _EPS:
            # Fills arrive through myTrades; keep the record open until they do.
            return
        order.state = final
        order.ended_at = order.ended_at or (
            _ms(entry["updateTime"]) if entry.get("updateTime") else self._clock()
        )
        order.add_event(self._clock(), "reconciled", f"exchange status {status}")
        if final == "cancelled":
            self._count("cancelled")
        self._mark(order)

    # --- shadows ---------------------------------------------------------------------

    def _evaluate_shadows(self, snapshot: OrderBookSnapshot) -> tuple[TickRecord, list[ShadowFill]]:
        self._tick_seq += 1
        snap_at = snapshot.timestamp
        window = [o for o in self._orders.values() if in_window(o, snap_at, self._last_snapshot_at)]
        bid = snapshot.bids[0] if snapshot.bids else None
        ask = snapshot.asks[0] if snapshot.asks else None
        tick = TickRecord(
            session_id=self._session.session_id,
            seq=self._tick_seq,
            at=snap_at,
            best_bid=bid.price if bid else None,
            bid_size=bid.size if bid else None,
            best_ask=ask.price if ask else None,
            ask_size=ask.size if ask else None,
            open_orders=tuple(o.client_order_id for o in window),
        )
        fills: list[ShadowFill] = []
        for shadow in self._shadows:
            fills.extend(
                shadow.evaluate(
                    snapshot, window, tick_seq=self._tick_seq, session_id=self._session.session_id
                )
            )
        self._last_snapshot_at = snap_at
        self._last_mid = mid_price(snapshot)
        if self._metrics:
            for fill in fills:
                prom.TESTNET_SHADOW_FILLS.labels(fill_model=fill.fill_model).inc()
        return tick, fills

    def shadow_filled(self) -> dict[str, dict[str, float]]:
        return {s.fill_model: dict(s.filled) for s in self._shadows}

    # --- quoting -----------------------------------------------------------------------

    async def _quote(self, snapshot: OrderBookSnapshot) -> None:
        rules = self._rules
        inventory = self._inventory
        now = self._clock()
        position = inventory.to_position(now)
        quotes = self._strategy.generate_quotes(snapshot, position)
        approved = filter_quotes_by_position_limit(
            quotes,
            position,
            self._settings.max_position_base,
            self._settings.max_position_notional,
            maker_fee_bps=self._maker_bps,
        )
        targets: dict[str, tuple[float, float, Quote]] = {}
        for quote in approved:
            side = "bid" if quote.side == QuoteSide.BID else "ask"
            price = round_price(quote.price, side, rules)
            size = round_size(quote.size, rules)
            reason = check_minimums(price, size, rules)
            if reason is not None:
                self._count(f"skipped_{reason}")
                continue
            if not self._fundable(side, price, size):
                self._count("skipped_unfundable")
                continue
            targets[side] = (price, size, quote)

        for side in ("bid", "ask"):
            resting = [o for o in self.open_orders if o.side == side]
            target = targets.get(side)
            keep: TestnetOrder | None = None
            for order in resting:
                if order.state == "unknown":
                    continue  # wait for reconciliation before touching it
                moved = target is None or (
                    abs(order.price - target[0]) / target[0] * 10_000
                    > self._settings.testnet_replace_threshold_bps
                )
                if keep is None and not moved:
                    keep = order
                    continue
                await self._cancel(order)
            if (
                target is not None
                and keep is None
                and not any(o.state == "unknown" for o in resting)
            ):
                await self._place(side, *target)

    def _fundable(self, side: str, price: float, size: float) -> bool:
        if not self._account_free:
            return True
        locked_by_us = sum(
            (o.size - o.filled_size) * (o.price if side == "bid" else 1.0)
            for o in self.open_orders
            if o.side == side
        )
        if side == "bid":
            return self._account_free.get(self._quote_asset, 0.0) + locked_by_us >= price * size
        return self._account_free.get(self._base_asset, 0.0) + locked_by_us >= size

    async def _place(self, side: str, price: float, size: float, quote: Quote) -> None:
        rules = self._rules
        self._seq += 1
        cid = f"{TAG_PREFIX}{self._session.session_id}-{self._seq}"
        now = self._clock()
        order = TestnetOrder(
            client_order_id=cid,
            session_id=self._session.session_id,
            side=side,
            price=price,
            size=size,
            placed_at=now,
            quote_id=quote.quote_id,
        )
        order.add_event(now, "placed", f"{side} {size:g} @ {price:.2f}")
        self._orders[cid] = order
        self._mark(order)
        try:
            ack = await self._client.place_limit_maker(
                self._symbol,
                side,
                format_step(price, rules.tick_size),
                format_step(size, rules.step_size),
                cid,
            )
        except OrderRejected as exc:
            done = self._clock()
            order.state, order.reject_reason, order.ended_at = "rejected", exc.reason, done
            order.add_event(done, "reject", exc.reason)
            self._count("rejected_post_only" if exc.reason == "post_only" else "rejected_other")
            if exc.reason == "unfundable":
                self._need_reconcile = True
            return
        except TestnetUnavailable as exc:
            order.state = "unknown"
            order.add_event(self._clock(), "request_lost", "no answer; reconciling")
            self._need_reconcile = True
            if not exc.maybe_executed:
                order.state, order.reject_reason, order.ended_at = (
                    "rejected",
                    "other",
                    self._clock(),
                )
            raise
        self._count("placed")
        order.exchange_order_id = int(ack["orderId"])
        self._by_exchange_id[order.exchange_order_id] = cid
        order.acked_at = self._clock()
        order.state = "open"
        order.add_event(order.acked_at, "ack")
        if self._metrics:
            prom.TESTNET_ORDERS.labels(side=side).inc()

    async def _cancel(self, order: TestnetOrder) -> None:
        now = self._clock()
        order.add_event(now, "cancel_requested")
        self._mark(order)
        try:
            await self._client.cancel_order(self._symbol, order.client_order_id)
        except UnknownOrder:
            # Filled (or gone) before the cancel arrived: find out which.
            try:
                entry = await self._client.get_order(self._symbol, order.client_order_id)
            except UnknownOrder:
                await self._testnet_reset("a recorded order no longer exists on the exchange")
                return
            if entry.get("status") == "FILLED":
                order.add_event(self._clock(), "cancel_too_late", "filled before the cancel")
                self._count("cancel_too_late")
                await self._sync_fills_now()
            self._apply_exchange_state(order, entry)
            return
        except TestnetUnavailable:
            self._need_reconcile = True
            raise
        done = self._clock()
        order.state = "cancelled"
        order.ended_at = done
        order.add_event(done, "cancelled")
        self._count("cancelled")

    async def _sync_fills_now(self) -> None:
        """Pick up the fills of an order that filled during a cancel race."""
        await self._read_fills()

    # --- stopping ------------------------------------------------------------------------

    async def cancel_all(self) -> bool:
        """Cancel every tagged open order and confirm with the exchange (FR-015)."""
        try:
            return await self._cancel_all()
        except _SessionReset:
            return await self._cancel_all()  # the new session starts with no orders

    async def _cancel_all(self) -> bool:
        try:
            for order in list(self.open_orders):
                if order.state == "unknown":
                    continue
                await self._cancel(order)
            for _ in range(_CANCEL_CONFIRM_ATTEMPTS):
                remaining = [
                    e
                    for e in await self._client.open_orders(self._symbol)
                    if e.get("clientOrderId", "").startswith(TAG_PREFIX)
                ]
                if not remaining:
                    for order in list(self.open_orders):
                        await self._resolve_missing(order)
                    self._reconcile = ReconcileState(
                        last_at=self._clock(), ok=True, unexplained=self._reconcile.unexplained
                    )
                    return True
                for entry in remaining:
                    cid = entry["clientOrderId"]
                    order = self._orders.get(cid)
                    if order is not None:
                        await self._cancel(order)
                    else:
                        try:
                            await self._client.cancel_order(self._symbol, cid)
                        except UnknownOrder:
                            pass
        except (TestnetUnavailable, RateLimited) as exc:
            self._need_reconcile = True
            self._set_state("cancel_pending", f"cancel pending: {exc}")
            return False
        self._set_state("cancel_pending", "orders still open after cancelling")
        return False

    async def shutdown(self) -> None:
        if self._session is None:
            self._set_state(
                "stopped" if self._state != "auth_failed" else self._state, self._reason
            )
            return
        for _ in range(3):
            if await self.cancel_all():
                break
            await asyncio.sleep(1)
        await self._flush()
        self._session.ended_at = self._clock()
        self._session.end_reason = "stopped"
        await asyncio.to_thread(self._repository.save_testnet_session, self._session)
        if self._state != "cancel_pending":
            self._set_state("stopped")

    # --- reconciliation -----------------------------------------------------------------

    async def reconcile(self) -> None:
        """Full check (FR-012): clock, balances, unknown orders, trade history, unexplained."""
        try:
            await self._reconcile_once()
        except _SessionReset:
            await self._flush()

    async def _reconcile_once(self) -> None:
        self._need_reconcile = False
        self._last_reconcile_mono = time.monotonic()
        await self._client.sync_time()
        self._read_account(await self._client.account())
        for order in list(self.open_orders):
            if order.state == "unknown" or order.exchange_order_id is None:
                await self._resolve_missing(order)
        if self._first_trade_id is not None:
            history = await self._client.my_trades(
                self._symbol, from_id=self._first_trade_id, limit=1
            )
            if not history or int(history[0]["id"]) != self._first_trade_id:
                await self._testnet_reset("the session's trade history disappeared")
                return
        unknown = sum(1 for o in self.open_orders if o.state == "unknown")
        await self._record_comparison_metrics()
        self._reconcile = ReconcileState(
            last_at=self._clock(),
            ok=unknown == 0,
            unexplained=self._counters.get("unexplained", 0),
        )

    async def _testnet_reset(self, reason: str) -> None:
        """FR-016: the testnet was reset; end this session and start a new one."""
        logger.warning("testnet_reset_detected", reason=reason)
        now = self._clock()
        for order in self.open_orders:
            order.state = "cancelled"
            order.ended_at = order.ended_at or now
            order.add_event(now, "testnet_reset", reason)
            self._mark(order)
        await self._flush()
        self._count("resets")
        self._session.ended_at = now
        self._session.end_reason = "reset"
        await asyncio.to_thread(self._repository.save_testnet_session, self._session)
        self._read_account(await self._client.account())
        await self._open_session()
        raise _SessionReset(reason)

    # --- metrics ---------------------------------------------------------------------------

    async def _record_comparison_metrics(self) -> None:
        if not self._metrics or self._session is None:
            return
        sid = self._session.session_id
        orders = await asyncio.to_thread(
            self._repository.get_testnet_orders, session_id=sid, limit=None
        )
        fills = await asyncio.to_thread(
            self._repository.get_testnet_fills, session_id=sid, limit=None
        )
        shadow = await asyncio.to_thread(self._repository.get_testnet_shadow_fills, sid)
        for item in compare(orders, fills, shadow, last_mid=self._last_mid):
            if item.paper_fill_rate is not None:
                prom.TESTNET_FILL_RATE.labels(source="paper", fill_model=item.fill_model).set(
                    item.paper_fill_rate
                )
                prom.TESTNET_FILL_RATE.labels(source="real", fill_model=item.fill_model).set(
                    item.real_fill_rate
                )
            prom.TESTNET_PNL_GAP.labels(fill_model=item.fill_model).set(item.pnl_gap)

    def _record_metrics(self) -> None:
        if not self._metrics:
            return
        prom.TESTNET_OPEN_ORDERS.set(len(self.open_orders))
        ratio = self._client.weight_used_ratio
        if ratio is not None:
            prom.TESTNET_WEIGHT_RATIO.set(ratio)
