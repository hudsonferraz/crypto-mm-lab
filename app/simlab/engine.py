"""Deterministic paper simulation of one recording (specs/009 FR-007, FR-008, D4, D6).

Reuses the live loop's pieces: strategies, the paper broker, its fill models and
the cash-account risk filter. Same recording and settings give the same result.
"""

from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Literal

from app.config.settings import StrategyName
from app.execution.fill_calibration import load_fill_calibration
from app.execution.paper_broker import PaperBroker
from app.market_data.orderbook import mid_price
from app.models.domain import Fill, QuoteSide
from app.risk.limits import filter_quotes_by_position_limit
from app.simlab.markets import quote_size_for, round_quote_price
from app.simlab.recording import MIN_SNAPSHOTS, Recording
from app.strategies.inventory_skew import InventorySkewStrategy
from app.strategies.pure_market_making import PureMarketMakingStrategy
from app.strategies.volatility_spread import VolatilitySpreadStrategy

LabFillModel = Literal[
    "full_cross_fill", "partial_fill", "latency_prob_fill", "calibrated_fill", "trade_fill"
]
LAB_STRATEGIES: tuple[StrategyName, ...] = ("pure_mm", "inventory_skew", "volatility_spread")
LAB_FILL_MODELS: tuple[LabFillModel, ...] = (
    "full_cross_fill",
    "partial_fill",
    "latency_prob_fill",
    "calibrated_fill",
    "trade_fill",
)

START_VALUE = 10_000.0
MAKER_FEE_BPS = 2.0
MAX_SERIES_POINTS = 300
MAX_FILLS_LISTED = 500
CALIBRATION_PATH = "app/execution/fill_calibration.json"


class SimulationFailed(Exception):
    """The recording cannot give a meaningful result; message is visitor-safe."""


@dataclass(frozen=True, slots=True)
class SimSettings:
    strategy: StrategyName
    spread_bps: float
    fill_model: LabFillModel


@dataclass(slots=True)
class SimulationResult:
    start_value: float
    final_value: float
    hold_value: float
    edge_vs_hold: float
    fees: float
    fill_count: int
    buy_count: int
    sell_count: int
    quote_size: float
    snapshot_count: int
    trade_count: int
    duration_sec: float
    start_mid: float
    end_mid: float
    start_base: float
    final_base: float
    series: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _build_strategy(settings: SimSettings, symbol: str, size: float, base0: float):
    if settings.strategy == "pure_mm":
        return PureMarketMakingStrategy(symbol, settings.spread_bps, size)
    if settings.strategy == "inventory_skew":
        # Skews back towards the balanced start rather than towards zero.
        return InventorySkewStrategy(symbol, settings.spread_bps, size, base0, 2 * base0, 20.0)
    if settings.strategy == "volatility_spread":
        return VolatilitySpreadStrategy(symbol, settings.spread_bps, size, 20, 1.0)
    raise SimulationFailed(f"unknown strategy: {settings.strategy}")


def _downsample(points: list[dict], limit: int = MAX_SERIES_POINTS) -> list[dict]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def simulate(recording: Recording, settings: SimSettings) -> SimulationResult:
    snapshots = sorted(
        (s for s in recording.snapshots if not s.is_stale and mid_price(s)),
        key=lambda s: s.timestamp,
    )
    if len(snapshots) < MIN_SNAPSHOTS:
        raise SimulationFailed(
            f"only {len(snapshots)} usable order book snapshots were recorded; "
            f"at least {MIN_SNAPSHOTS} are needed for a meaningful result"
        )

    start_mid = mid_price(snapshots[0])
    base0 = (START_VALUE / 2) / start_mid
    quote0 = START_VALUE / 2
    size = quote_size_for(start_mid, recording.rules)
    rules = recording.rules
    symbol = recording.symbol
    trade_mode = settings.fill_model == "trade_fill"

    broker = PaperBroker(
        symbol=symbol,
        initial_quote_balance=quote0,
        maker_fee_bps=MAKER_FEE_BPS,
        fill_mode="full_cross_fill" if trade_mode else settings.fill_model,
        fill_latency_ticks=2,
        fill_probability=0.7,
        calibration=(
            load_fill_calibration(CALIBRATION_PATH)
            if settings.fill_model == "calibrated_fill"
            else None
        ),
    )
    # D6: start half in base at the first mid, so holding is a fair baseline.
    broker.inventory.restore_state(
        base_amount=base0,
        quote_amount=quote0,
        average_entry_price=start_mid,
        realized_pnl=0.0,
        total_fees=0.0,
        last_updated=None,
    )
    strategy = _build_strategy(settings, symbol, size, base0)

    trades = recording.trades
    trade_times = [trade.timestamp for trade in trades]
    t0 = snapshots[0].timestamp
    fills: list[Fill] = []
    series: list[dict] = []
    previous_at = None

    for index, snapshot in enumerate(snapshots):
        at = snapshot.timestamp
        if trade_mode:
            if previous_at is not None:
                lo = bisect_right(trade_times, previous_at)
                hi = bisect_right(trade_times, at)
                fills.extend(broker.apply_trade_fills(trades[lo:hi], at))
        else:
            fills.extend(broker.apply_fills(snapshot))
        previous_at = at

        inventory = broker.inventory
        mid = mid_price(snapshot)
        value = inventory.base_amount * mid + inventory.quote_amount
        hold = base0 * mid + quote0
        series.append(
            {
                "t": round((at - t0).total_seconds(), 3),
                "value_pnl": value - START_VALUE,
                "hold_pnl": hold - START_VALUE,
                "base": inventory.base_amount,
            }
        )

        position = inventory.to_position(at)
        quotes = []
        for quote in strategy.generate_quotes(snapshot, position):
            quotes.append(
                replace(
                    quote,
                    price=round_quote_price(quote.price, quote.side, rules),
                    quote_id=f"q{index}-{quote.side.value}",
                )
            )
        approved = filter_quotes_by_position_limit(
            quotes,
            position,
            2 * base0,
            2 * START_VALUE,
            maker_fee_bps=MAKER_FEE_BPS,
        )
        broker.submit_quotes(approved)

    end_mid = mid_price(snapshots[-1])
    inventory = broker.inventory
    final_value = inventory.base_amount * end_mid + inventory.quote_amount
    hold_value = base0 * end_mid + quote0
    notes = []
    if trade_mode and not trades:
        notes.append("No public trades were recorded, so the trade-driven model could not fill.")

    return SimulationResult(
        start_value=START_VALUE,
        final_value=final_value,
        hold_value=hold_value,
        edge_vs_hold=final_value - hold_value,
        fees=inventory.total_fees,
        fill_count=len(fills),
        buy_count=sum(1 for fill in fills if fill.side == QuoteSide.BID),
        sell_count=sum(1 for fill in fills if fill.side == QuoteSide.ASK),
        quote_size=size,
        snapshot_count=len(snapshots),
        trade_count=len(trades),
        duration_sec=round((snapshots[-1].timestamp - t0).total_seconds(), 1),
        start_mid=start_mid,
        end_mid=end_mid,
        start_base=base0,
        final_base=inventory.base_amount,
        series=_downsample(series),
        fills=[
            {
                "t": round((fill.timestamp - t0).total_seconds(), 3),
                "side": "buy" if fill.side == QuoteSide.BID else "sell",
                "price": fill.price,
                # Partial fills leave float dust (0.00124 - x); 12 decimals is far below any step.
                "size": round(fill.size, 12),
                "fee": fill.fee,
            }
            for fill in fills[:MAX_FILLS_LISTED]
        ],
        notes=notes,
    )
