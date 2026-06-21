import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.adapters.cex.ccxt_adapter import CcxtAdapter
from app.adapters.dex.web3_pool_adapter import Web3PoolAdapter
from app.analytics.performance_report import format_performance_report
from app.analytics.pnl import compute_pnl_snapshot
from app.config.settings import Settings
from app.execution.paper_broker import PaperBroker
from app.market_data.orderbook import mid_price
from app.models.domain import AmmPoolSnapshot, Opportunity, OrderBookSnapshot, PnLSnapshot, Position
from app.observability import metrics as prom
from app.risk.kill_switch import KillSwitch
from app.risk.limits import filter_quotes_by_position_limit
from app.services.arbitrage_scanner import scan_arbitrage_opportunities
from app.storage.repository import Repository
from app.strategies.factory import build_strategy

logger = structlog.get_logger(__name__)


class MarketMakerLoop:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._data_source = CcxtAdapter(settings.exchange, settings.symbol)
        self._compare_source = CcxtAdapter(settings.exchange, settings.cex_compare_symbol)
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
    def open_quote_count(self) -> int:
        return self._broker.open_quote_count

    @property
    def repository(self) -> Repository:
        return self._repository

    def cancel_all_quotes(self) -> None:
        self._broker.cancel_all_quotes()

    def initialize(self) -> None:
        db_path = self._settings.db_url.replace("sqlite:///", "")
        if db_path.startswith("./"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
            await self._tick_once()
            await asyncio.sleep(self._settings.poll_interval_sec)

    async def _tick_once(self) -> None:
        start = time.perf_counter()
        now = datetime.now(UTC)
        snapshot = await self._data_source.fetch_orderbook()
        self._last_snapshot = snapshot

        if self._settings.dex_enabled and self._pool_adapter is not None:
            compare_snapshot, pool_snapshot = await asyncio.gather(
                self._compare_source.fetch_orderbook(),
                self._pool_adapter.fetch_pool_snapshot(),
            )
            self._last_pool_snapshot = pool_snapshot
            self._last_compare_mid = mid_price(compare_snapshot)
            self._last_opportunities = self._scan_opportunities(
                cex_mid=self._last_compare_mid,
                pool_snapshot=pool_snapshot,
                eth_price_usd=self._last_compare_mid or 0.0,
            )
            if self._last_opportunities:
                self._repository.save_opportunities(self._last_opportunities)

        fills = self._broker.apply_fills(snapshot)
        if fills:
            self._repository.save_fills(fills)

        position = self._broker.inventory.to_position(now)
        self._last_position = position

        pnl = compute_pnl_snapshot(self._broker.inventory, snapshot, now)
        self._last_pnl = pnl

        if not self._kill_switch.active:
            quotes = self._strategy.generate_quotes(snapshot, position)
            approved_quotes = filter_quotes_by_position_limit(
                quotes,
                position,
                self._settings.max_position_base,
                self._settings.max_position_notional,
                maker_fee_bps=self._settings.maker_fee_bps,
            )
            self._broker.submit_quotes(approved_quotes)
            if approved_quotes:
                self._repository.save_quotes(approved_quotes)
        else:
            self._broker.cancel_all_quotes()

        self._repository.save_orderbook_snapshot(snapshot)
        self._repository.save_position(position)
        self._repository.save_pnl_snapshot(pnl)

        self._tick += 1
        self._last_tick_at = now

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
        pool_snapshot: AmmPoolSnapshot,
        eth_price_usd: float,
    ) -> list[Opportunity]:
        if cex_mid is None or cex_mid <= 0 or pool_snapshot.is_stale:
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
