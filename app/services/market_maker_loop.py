import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime

import structlog

from app.adapters.cex.market_data_factory import build_market_data_source
from app.adapters.dex.multicall import Web3Transport
from app.adapters.dex.v3_fixture_replay import V3FixtureReplay
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter, V3PoolUnavailable
from app.adapters.dex.web3_pool_adapter import Web3PoolAdapter
from app.analytics.performance_report import format_performance_report
from app.analytics.pnl import compute_pnl_snapshot
from app.config.settings import Settings
from app.execution.lp_paper_book import LpPaperBook
from app.execution.paper_broker import PaperBroker
from app.execution.tick_ids import new_tick_id
from app.market_data.orderbook import mid_price
from app.models.domain import (
    AmmPoolSnapshot,
    Opportunity,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
)
from app.models.v3 import LpPositionState, RebalanceEvent, V3PoolSnapshot
from app.observability import metrics as prom
from app.risk.kill_switch import KillSwitch
from app.risk.limits import filter_quotes_by_position_limit
from app.services.arbitrage_scanner import scan_arbitrage_opportunities, scan_v3_opportunities
from app.storage.repository import Repository
from app.strategies.concentrated_lp import ConcentratedLpStrategy
from app.strategies.factory import build_strategy

logger = structlog.get_logger(__name__)

# While replaying the V3 fixture, the loop stays on fixture data for the rest of
# the process: mixing fee-growth counters from two sources would corrupt accrual.
_V3_FIXTURE_NOTE = "live V3 RPC unavailable; replaying recorded mainnet observations"


def build_v3_source(settings: Settings) -> V3PoolAdapter:
    return V3PoolAdapter(
        Web3Transport(settings.eth_rpc_url, timeout_sec=settings.v3_rpc_timeout_sec),
        settings.v3_pool_address,
        window_words=settings.v3_window_words,
        window_refresh_ticks=settings.v3_window_refresh_ticks,
    )


def build_lp_book(settings: Settings) -> LpPaperBook:
    return LpPaperBook(
        strategy=ConcentratedLpStrategy(
            range_width_bps=settings.lp_range_width_bps,
            rebalance_threshold_bps=settings.lp_rebalance_threshold_bps,
            cooldown_sec=settings.lp_rebalance_cooldown_sec,
            prefer_initialized_ticks=settings.lp_prefer_initialized_ticks,
            snap_tolerance_spacings=settings.lp_snap_tolerance_spacings,
        ),
        capital_quote=settings.lp_capital_quote,
        gas_units=settings.lp_rebalance_gas_units,
        priority_fee_gwei=settings.lp_priority_fee_gwei,
        fallback_gas_price_gwei=settings.gas_price_gwei,
    )


class MarketMakerLoop:
    def __init__(self, settings: Settings, *, v3_source=None) -> None:
        self._settings = settings
        self._data_source = build_market_data_source(
            settings.exchange,
            settings.symbol,
            settings.market_data_mode,
            fixture_fallback_enabled=settings.fixture_fallback_enabled,
            fixture_path=settings.market_data_fixture_path,
        )
        self._compare_source = build_market_data_source(
            settings.exchange,
            settings.cex_compare_symbol,
            settings.market_data_mode,
            fixture_fallback_enabled=settings.fixture_fallback_enabled,
        )
        self._pool_adapter: Web3PoolAdapter | None = None
        if settings.dex_enabled:
            self._pool_adapter = Web3PoolAdapter(
                rpc_url=settings.eth_rpc_url,
                pool_address=settings.dex_pool_address,
                base_decimals=settings.pool_base_decimals,
                quote_decimals=settings.pool_quote_decimals,
                amm_fee_bps=settings.amm_fee_bps,
            )
        self._strategy = build_strategy(settings)
        self._broker = PaperBroker(
            symbol=settings.symbol,
            initial_quote_balance=settings.initial_quote_balance,
            maker_fee_bps=settings.maker_fee_bps,
            fill_mode=settings.fill_mode,
            fill_latency_ticks=settings.fill_latency_ticks,
            fill_probability=settings.fill_probability,
        )
        self._kill_switch = KillSwitch()
        self._repository = Repository(settings.db_url)
        self._running = False
        self._task: asyncio.Task | None = None
        self._tick = 0
        self._last_snapshot: OrderBookSnapshot | None = None
        self._last_pool_snapshot: AmmPoolSnapshot | None = None
        self._last_compare_mid: float | None = None
        self._last_opportunities: list[Opportunity] = []
        self._last_position: Position | None = None
        self._last_pnl: PnLSnapshot | None = None
        self._last_tick_at: datetime | None = None
        self._last_tick_id: str | None = None
        self._last_error: str | None = None
        self._consecutive_errors = 0
        self._max_consecutive_errors = 3

        self._v3_source = None
        self._lp_book: LpPaperBook | None = None
        if settings.v3_active:
            self._v3_source = v3_source or build_v3_source(settings)
            if settings.lp_active:
                self._lp_book = build_lp_book(settings)
        self._v3_fixture_mode = False
        self._v3_status_note: str | None = None
        self._last_v3_snapshot: V3PoolSnapshot | None = None
        self._last_lp_state: LpPositionState | None = None
        self._started_at: datetime | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def task_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_snapshot(self) -> OrderBookSnapshot | None:
        return self._last_snapshot

    @property
    def last_pool_snapshot(self) -> AmmPoolSnapshot | None:
        return self._last_pool_snapshot

    @property
    def last_compare_mid(self) -> float | None:
        return self._last_compare_mid

    @property
    def last_opportunities(self) -> list[Opportunity]:
        return self._last_opportunities

    @property
    def last_position(self) -> Position | None:
        return self._last_position

    @property
    def last_pnl(self) -> PnLSnapshot | None:
        return self._last_pnl

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    @property
    def last_tick_id(self) -> str | None:
        return self._last_tick_id

    @property
    def open_quote_count(self) -> int:
        return self._broker.open_quote_count

    @property
    def open_quotes(self):
        return self._broker.open_quotes

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def last_v3_snapshot(self) -> V3PoolSnapshot | None:
        return self._last_v3_snapshot

    @property
    def last_lp_state(self) -> LpPositionState | None:
        return self._last_lp_state

    @property
    def lp_book(self) -> LpPaperBook | None:
        return self._lp_book

    @property
    def v3_status_note(self) -> str | None:
        return self._v3_status_note

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def repository(self) -> Repository:
        return self._repository

    def cancel_all_quotes(self) -> None:
        self._broker.cancel_all_quotes()

    def _commit_tick_state(
        self,
        *,
        tick_id: str,
        snapshot: OrderBookSnapshot,
        position: Position,
        pnl: PnLSnapshot,
        opportunities: list[Opportunity],
        now: datetime,
    ) -> None:
        self._last_snapshot = replace(snapshot, tick_id=tick_id)
        self._last_position = replace(position, tick_id=tick_id)
        self._last_pnl = replace(pnl, tick_id=tick_id)
        self._last_opportunities = [
            replace(opportunity, tick_id=tick_id) for opportunity in opportunities
        ]
        self._last_tick_id = tick_id
        self._tick += 1
        self._last_tick_at = now

    def initialize(self) -> None:
        self._repository.initialize()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._data_source.close()
        self._compare_source.close()
        self._repository.close()

    async def run_once(self) -> None:
        await self._tick_once()

    async def _run(self) -> None:
        while self._running:
            try:
                await self._tick_once()
                self._consecutive_errors = 0
                self._last_error = None
            except Exception as error:
                self._consecutive_errors += 1
                self._last_error = str(error)
                logger.exception(
                    "tick_failed",
                    error=str(error),
                    consecutive_errors=self._consecutive_errors,
                )
                if self._consecutive_errors >= self._max_consecutive_errors:
                    self._running = False
                    break
                backoff_seconds = min(2**self._consecutive_errors, 30)
                await asyncio.sleep(backoff_seconds)
                continue

            await asyncio.sleep(self._settings.poll_interval_sec)

    async def _tick_once(self) -> None:
        start = time.perf_counter()
        now = datetime.now(UTC)
        snapshot = await self._data_source.fetch_orderbook()
        self._last_snapshot = snapshot

        compare_snapshot: OrderBookSnapshot | None = None
        tick_opportunities: list[Opportunity] = []
        if self._settings.dex_enabled and self._pool_adapter is not None:
            compare_snapshot, pool_snapshot = await asyncio.gather(
                self._compare_source.fetch_orderbook(),
                self._pool_adapter.fetch_pool_snapshot(),
            )
            self._last_pool_snapshot = pool_snapshot
            self._last_compare_mid = mid_price(compare_snapshot)
            tick_opportunities = self._scan_opportunities(
                cex_mid=self._last_compare_mid,
                primary_is_stale=snapshot.is_stale,
                compare_is_stale=compare_snapshot.is_stale,
                pool_snapshot=pool_snapshot,
                eth_price_usd=self._last_compare_mid or 0.0,
            )
            self._last_opportunities = tick_opportunities

        compare_is_stale = compare_snapshot is None or compare_snapshot.is_stale
        v3_snapshot, lp_state, lp_events, v3_opportunities, lp_checkpoint = await self._v3_step(
            compare_mid=None if compare_is_stale else self._last_compare_mid,
            cex_is_stale=snapshot.is_stale or compare_is_stale,
        )
        if v3_opportunities:
            tick_opportunities = [*tick_opportunities, *v3_opportunities]
            self._last_opportunities = tick_opportunities
        v3_persist = dict(v3_snapshot=v3_snapshot, lp_state=lp_state, lp_rebalances=lp_events)

        if self._started_at is None:
            self._started_at = now

        if snapshot.is_stale:
            broker_checkpoint = self._broker.checkpoint()
            try:
                self._broker.cancel_all_quotes()
                if self._settings.metrics_enabled:
                    prom.STALE_TICKS.inc()
                position = self._broker.inventory.to_position(now)
                pnl = compute_pnl_snapshot(self._broker.inventory, snapshot, now)
                tick_id = new_tick_id()
                self._repository.persist_tick(
                    tick_id=tick_id,
                    snapshot=snapshot,
                    fills=[],
                    quotes=[],
                    position=position,
                    pnl=pnl,
                    opportunities=tick_opportunities,
                    **v3_persist,
                )
            except Exception:
                self._broker.restore_checkpoint(broker_checkpoint)
                self._restore_lp(lp_checkpoint)
                raise
            self._commit_v3_state(tick_id, v3_snapshot, lp_state)
            self._commit_tick_state(
                tick_id=tick_id,
                snapshot=snapshot,
                position=position,
                pnl=pnl,
                opportunities=tick_opportunities,
                now=now,
            )
            if self._settings.metrics_enabled:
                self._record_metrics(start, [], position, pnl)
            return

        broker_checkpoint = self._broker.checkpoint()
        try:
            fills = self._broker.apply_fills(snapshot)
            position = self._broker.inventory.to_position(now)
            pnl = compute_pnl_snapshot(self._broker.inventory, snapshot, now)

            submitted_quotes: list[Quote] = []
            if not self._kill_switch.active:
                quotes = self._strategy.generate_quotes(snapshot, position)
                approved_quotes = filter_quotes_by_position_limit(
                    quotes,
                    position,
                    self._settings.max_position_base,
                    self._settings.max_position_notional,
                    maker_fee_bps=self._settings.maker_fee_bps,
                )
                submitted_quotes = self._broker.submit_quotes(approved_quotes)
            else:
                self._broker.cancel_all_quotes()

            tick_id = new_tick_id()
            self._repository.persist_tick(
                tick_id=tick_id,
                snapshot=snapshot,
                fills=fills,
                quotes=submitted_quotes,
                position=position,
                pnl=pnl,
                opportunities=tick_opportunities,
                **v3_persist,
            )
        except Exception:
            self._broker.restore_checkpoint(broker_checkpoint)
            self._restore_lp(lp_checkpoint)
            raise

        self._commit_v3_state(tick_id, v3_snapshot, lp_state)
        self._commit_tick_state(
            tick_id=tick_id,
            snapshot=snapshot,
            position=position,
            pnl=pnl,
            opportunities=tick_opportunities,
            now=now,
        )

        if self._settings.metrics_enabled:
            self._record_metrics(start, fills, position, pnl)

        if self._tick % self._settings.report_interval_ticks == 0:
            report = format_performance_report(
                tick=self._tick,
                snapshot=snapshot,
                position=position,
                pnl=pnl,
                open_quotes=self._broker.open_quote_count,
                kill_switch_active=self._kill_switch.active,
                pool_snapshot=self._last_pool_snapshot,
                compare_mid=self._last_compare_mid,
                opportunities=self._last_opportunities,
            )
            logger.info("performance_report", report=report)

    def _restore_lp(self, checkpoint) -> None:
        if checkpoint is not None and self._lp_book is not None:
            self._lp_book.restore(checkpoint)

    def _commit_v3_state(
        self,
        tick_id: str,
        v3_snapshot: V3PoolSnapshot | None,
        lp_state: LpPositionState | None,
    ) -> None:
        if v3_snapshot is not None:
            self._last_v3_snapshot = replace(v3_snapshot, tick_id=tick_id)
        if lp_state is not None:
            self._last_lp_state = replace(lp_state, tick_id=tick_id)

    async def _fetch_v3_snapshot(self) -> V3PoolSnapshot | None:
        assert self._v3_source is not None
        bounds = self._lp_book.bounds if self._lp_book is not None else None
        if not self._v3_fixture_mode:
            try:
                snapshot = await self._v3_source.fetch_snapshot(bounds)
                self._v3_status_note = None
                return snapshot
            except V3PoolUnavailable as error:
                self._v3_status_note = str(error)
                if self._settings.metrics_enabled:
                    prom.V3_SNAPSHOT_FAILURES.inc()
                if not self._settings.fixture_fallback_enabled:
                    return None
                try:
                    self._v3_source = V3FixtureReplay()
                except (OSError, ValueError) as fixture_error:
                    logger.warning("v3_fixture_unavailable", error=str(fixture_error))
                    return None
                self._v3_fixture_mode = True
                logger.warning("v3_fixture_fallback", reason=str(error))
        self._v3_status_note = _V3_FIXTURE_NOTE
        return await self._v3_source.fetch_snapshot(bounds)

    async def _v3_step(
        self, *, compare_mid: float | None, cex_is_stale: bool
    ) -> tuple[
        V3PoolSnapshot | None,
        LpPositionState | None,
        list[RebalanceEvent],
        list[Opportunity],
        object | None,
    ]:
        """Read the V3 pool, advance the paper LP book, and scan V3 arbitrage."""
        if self._v3_source is None:
            return None, None, [], [], None
        v3_snapshot = await self._fetch_v3_snapshot()
        if v3_snapshot is None:
            return None, None, [], [], None
        if self._settings.metrics_enabled and (v3_snapshot.is_stale or v3_snapshot.is_incomplete):
            prom.V3_SNAPSHOT_FAILURES.inc()

        lp_state = None
        lp_events: list[RebalanceEvent] = []
        lp_checkpoint = None
        if self._lp_book is not None:
            lp_checkpoint = self._lp_book.checkpoint()
            lp_state, lp_events = self._lp_book.step(
                v3_snapshot,
                center_price=compare_mid,
                kill_switch_active=self._kill_switch.active,
            )

        opportunities: list[Opportunity] = []
        if compare_mid is not None:
            gas_price_gwei = self._settings.gas_price_gwei
            if v3_snapshot.base_fee_wei is not None:
                priority_gwei = self._settings.lp_priority_fee_gwei
                gas_price_gwei = v3_snapshot.base_fee_wei / 1e9 + priority_gwei
            opportunities = scan_v3_opportunities(
                cex_mid=compare_mid,
                snapshot=v3_snapshot,
                trial_trade_size=self._settings.arbitrage_trial_trade_size,
                cex_taker_fee_bps=self._settings.taker_fee_bps,
                gas_limit=self._settings.gas_limit_units,
                gas_price_gwei=gas_price_gwei,
                min_edge_bps=self._settings.arbitrage_min_edge_bps,
                cex_is_stale=cex_is_stale,
            )

        if self._settings.metrics_enabled:
            self._record_v3_metrics(v3_snapshot, lp_state, lp_events)
        return v3_snapshot, lp_state, lp_events, opportunities, lp_checkpoint

    def _record_v3_metrics(
        self,
        snapshot: V3PoolSnapshot,
        lp_state: LpPositionState | None,
        lp_events: list[RebalanceEvent],
    ) -> None:
        prom.V3_SNAPSHOT_STALE.set(1 if snapshot.is_stale else 0)
        prom.V3_BLOCK_NUMBER.set(snapshot.block_number)
        prom.V3_POOL_PRICE.set(snapshot.price)
        if lp_state is not None:
            prom.LP_IN_RANGE.set(1 if lp_state.status == "in_range" else 0)
            prom.LP_FEES_QUOTE.set(lp_state.fees_quote)
            prom.LP_IL_QUOTE.set(lp_state.il_quote)
            prom.LP_NET_PNL_QUOTE.set(lp_state.net_pnl_quote)
        prom.LP_REBALANCES.inc(sum(1 for event in lp_events if event.reason != "open"))

    def _record_metrics(
        self,
        start: float,
        fills: list,
        position: Position,
        pnl: PnLSnapshot,
    ) -> None:
        elapsed = time.perf_counter() - start
        prom.TICK_LATENCY.observe(elapsed)
        prom.TICK_TOTAL.inc()
        prom.FILLS_TOTAL.inc(len(fills))
        prom.POSITION_BASE.set(position.base_amount)
        prom.POSITION_QUOTE.set(position.quote_amount)
        prom.PNL_TOTAL.set(pnl.total_pnl)
        prom.PNL_REALIZED.set(pnl.realized_pnl)
        prom.PNL_UNREALIZED.set(pnl.unrealized_pnl)
        prom.OPEN_QUOTES.set(self._broker.open_quote_count)
        prom.OPPORTUNITY_COUNT.set(len(self._last_opportunities))
        prom.KILL_SWITCH_ACTIVE.set(1 if self._kill_switch.active else 0)
        mid = mid_price(self._last_snapshot) if self._last_snapshot else None
        if mid is not None:
            prom.MID_PRICE.set(mid)

    def _scan_opportunities(
        self,
        *,
        cex_mid: float | None,
        primary_is_stale: bool,
        compare_is_stale: bool,
        pool_snapshot: AmmPoolSnapshot,
        eth_price_usd: float,
    ) -> list[Opportunity]:
        if (
            cex_mid is None
            or cex_mid <= 0
            or primary_is_stale
            or compare_is_stale
            or pool_snapshot.is_stale
        ):
            return []

        return scan_arbitrage_opportunities(
            cex_mid=cex_mid,
            pool_snapshot=pool_snapshot,
            trial_trade_size=self._settings.arbitrage_trial_trade_size,
            cex_taker_fee_bps=self._settings.taker_fee_bps,
            amm_fee_bps=self._settings.amm_fee_bps,
            gas_limit=self._settings.gas_limit_units,
            gas_price_gwei=self._settings.gas_price_gwei,
            eth_price_usd=eth_price_usd,
            min_edge_bps=self._settings.arbitrage_min_edge_bps,
        )
