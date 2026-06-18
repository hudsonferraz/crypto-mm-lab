import asyncio
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.adapters.cex.ccxt_adapter import CcxtAdapter
from app.analytics.performance_report import format_performance_report
from app.analytics.pnl import compute_pnl_snapshot
from app.config.settings import Settings
from app.execution.paper_broker import PaperBroker
from app.models.domain import OrderBookSnapshot, PnLSnapshot, Position
from app.risk.kill_switch import KillSwitch
from app.risk.limits import filter_quotes_by_position_limit
from app.storage.repository import Repository
from app.strategies.factory import build_strategy

logger = structlog.get_logger(__name__)


class MarketMakerLoop:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._data_source = CcxtAdapter(settings.exchange, settings.symbol)
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
        self._repository.close()

    async def run_once(self) -> None:
        await self._tick_once()

    async def _run(self) -> None:
        while self._running:
            await self._tick_once()
            await asyncio.sleep(self._settings.poll_interval_sec)

    async def _tick_once(self) -> None:
        now = datetime.now(UTC)
        snapshot = await self._data_source.fetch_orderbook()
        self._last_snapshot = snapshot

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

        if self._tick % self._settings.report_interval_ticks == 0:
            report = format_performance_report(
                tick=self._tick,
                snapshot=snapshot,
                position=position,
                pnl=pnl,
                open_quotes=self._broker.open_quote_count,
                kill_switch_active=self._kill_switch.active,
            )
            logger.info("performance_report", report=report)
