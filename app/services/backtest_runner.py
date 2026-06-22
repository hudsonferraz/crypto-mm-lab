from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from app.analytics.backtest_metrics import BacktestMetrics, compute_backtest_metrics
from app.analytics.performance_report import format_backtest_report
from app.analytics.pnl import compute_pnl_snapshot
from app.config.settings import Settings
from app.execution.paper_broker import PaperBroker
from app.models.domain import Fill, OrderBookLevel, OrderBookSnapshot
from app.risk.limits import filter_quotes_by_position_limit
from app.storage.repository import Repository
from app.strategies.factory import build_strategy


@dataclass(frozen=True, slots=True)
class BacktestResult:
    metrics: BacktestMetrics
    fills: list[Fill]
    pnl_series: list[float]
    report: str


def _snapshot_from_row(
    symbol: str,
    best_bid: float,
    best_ask: float,
    timestamp: datetime,
    is_stale: bool,
) -> OrderBookSnapshot:
    spread = best_ask - best_bid
    bid_levels = (OrderBookLevel(best_bid, 1.0), OrderBookLevel(best_bid - spread * 0.1, 1.0))
    ask_levels = (OrderBookLevel(best_ask, 1.0), OrderBookLevel(best_ask + spread * 0.1, 1.0))
    return OrderBookSnapshot(
        symbol=symbol,
        bids=bid_levels,
        asks=ask_levels,
        timestamp=timestamp,
        is_stale=is_stale,
    )


def _snapshots_from_dataframe(symbol: str, frame: pl.DataFrame) -> list[OrderBookSnapshot]:
    snapshots: list[OrderBookSnapshot] = []
    columns = frame.select(["best_bid", "best_ask", "is_stale", "timestamp"]).rows()
    for best_bid, best_ask, is_stale, timestamp_value in columns:
        timestamp_text = str(timestamp_value).replace("Z", "+00:00")
        timestamp = datetime.fromisoformat(timestamp_text)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        snapshots.append(
            _snapshot_from_row(
                symbol=symbol,
                best_bid=float(best_bid),
                best_ask=float(best_ask),
                timestamp=timestamp,
                is_stale=bool(is_stale),
            )
        )
    return snapshots


class BacktestRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._strategy = build_strategy(settings)
        self._broker = PaperBroker(
            symbol=settings.symbol,
            initial_quote_balance=settings.initial_quote_balance,
            maker_fee_bps=settings.maker_fee_bps,
            fill_mode=settings.fill_mode,
        )
        self._repository = Repository(settings.db_url)

    def run_from_repository(
        self,
        *,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
        symbol: str | None = None,
        limit: int | None = None,
    ) -> BacktestResult:
        self._repository.initialize()
        try:
            snapshots = self._repository.load_orderbook_snapshots(
                symbol=symbol or self._settings.symbol,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                limit=limit,
            )
            return self._run_snapshots(snapshots)
        finally:
            self._repository.close()

    def run_from_fixture(self, fixture_path: str | Path) -> BacktestResult:
        path = Path(fixture_path)
        if path.suffix == ".parquet":
            frame = pl.read_parquet(path)
        elif path.suffix == ".csv":
            frame = pl.read_csv(path)
        else:
            raise ValueError(f"Unsupported fixture format: {path.suffix}")

        snapshots = _snapshots_from_dataframe(self._settings.symbol, frame)
        return self._run_snapshots(snapshots)

    def _run_snapshots(self, snapshots: list[OrderBookSnapshot]) -> BacktestResult:
        if not snapshots:
            empty_metrics = compute_backtest_metrics(
                [],
                tick_count=0,
                fill_count=0,
                quote_count=0,
                final_base=0.0,
                final_quote=self._settings.initial_quote_balance,
            )
            return BacktestResult(
                metrics=empty_metrics,
                fills=[],
                pnl_series=[],
                report=format_backtest_report(empty_metrics, []),
            )

        all_fills: list[Fill] = []
        pnl_series: list[float] = []
        quote_count = 0

        for snapshot in snapshots:
            now = snapshot.timestamp

            if snapshot.is_stale:
                self._broker.cancel_all_quotes()
                position = self._broker.inventory.to_position(now)
                pnl = compute_pnl_snapshot(self._broker.inventory, snapshot, now)
                pnl_series.append(pnl.total_pnl)
                continue

            fills = self._broker.apply_fills(snapshot)
            all_fills.extend(fills)

            position = self._broker.inventory.to_position(now)
            pnl = compute_pnl_snapshot(self._broker.inventory, snapshot, now)
            pnl_series.append(pnl.total_pnl)

            quotes = self._strategy.generate_quotes(snapshot, position)
            approved = filter_quotes_by_position_limit(
                quotes,
                position,
                self._settings.max_position_base,
                self._settings.max_position_notional,
                maker_fee_bps=self._settings.maker_fee_bps,
            )
            quote_count += len(approved)
            self._broker.submit_quotes(approved)

        final_position = self._broker.inventory.to_position(snapshots[-1].timestamp)
        snapshot_timestamps = [snapshot.timestamp for snapshot in snapshots]
        metrics = compute_backtest_metrics(
            pnl_series,
            tick_count=len(snapshots),
            fill_count=len(all_fills),
            quote_count=quote_count,
            final_base=final_position.base_amount,
            final_quote=final_position.quote_amount,
            timestamps=snapshot_timestamps,
        )
        report = format_backtest_report(metrics, all_fills)
        return BacktestResult(
            metrics=metrics,
            fills=all_fills,
            pnl_series=pnl_series,
            report=report,
        )
