import json
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.analytics.arb_report import simulation_from_dict, simulation_to_dict
from app.analytics.lp_report import (
    lp_state_from_dict,
    lp_state_to_dict,
    rebalance_from_dict,
    rebalance_to_dict,
)
from app.analytics.mempool_report import (
    block_from_dict,
    block_to_dict,
    mode_change_to_dict,
    watched_from_dict,
    watched_to_dict,
)
from app.analytics.performance_report import (
    fill_to_dict,
    opportunity_to_dict,
    pnl_history_point,
    position_to_dict,
    quote_to_dict,
)
from app.analytics.testnet_report import (
    fill_from_dict as testnet_fill_from_dict,
)
from app.analytics.testnet_report import (
    fill_to_dict as testnet_fill_to_dict,
)
from app.analytics.testnet_report import (
    order_from_dict,
    order_to_dict,
    session_from_dict,
    session_to_dict,
    shadow_from_dict,
    shadow_to_dict,
    tick_from_dict,
    tick_to_dict,
)
from app.execution.tick_ids import new_tick_id
from app.models.domain import (
    ArbitrageDirection,
    Fill,
    Opportunity,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
    QuoteSide,
)
from app.models.flash_arb import SimulationResult
from app.models.mempool import BlockVisibility, ModeChange, WatchedSwap
from app.models.testnet import (
    ShadowFill,
    TestnetFill,
    TestnetOrder,
    TestnetSession,
    TickRecord,
)
from app.models.tick_audit import TickAuditBundle
from app.models.v3 import LpPositionState, RebalanceEvent, V3PoolSnapshot
from app.storage.database import create_db_engine


class Base(DeclarativeBase):
    pass


class OrderBookSnapshotRow(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    best_bid: Mapped[float] = mapped_column(Float, nullable=False)
    best_ask: Mapped[float] = mapped_column(Float, nullable=False)
    mid_price: Mapped[float] = mapped_column(Float, nullable=False)
    spread_bps: Mapped[float] = mapped_column(Float, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuoteRow(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    quote_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillRow(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    quote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_amount: Mapped[float] = mapped_column(Float, nullable=False)
    quote_amount: Mapped[float] = mapped_column(Float, nullable=False)
    average_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PnLSnapshotRow(Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpportunityRow(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    cex_mid: Mapped[float] = mapped_column(Float, nullable=False)
    amm_price: Mapped[float] = mapped_column(Float, nullable=False)
    trial_trade_size: Mapped[float] = mapped_column(Float, nullable=False)
    gross_edge: Mapped[float] = mapped_column(Float, nullable=False)
    cex_fee: Mapped[float] = mapped_column(Float, nullable=False)
    amm_fee: Mapped[float] = mapped_column(Float, nullable=False)
    gas_cost: Mapped[float] = mapped_column(Float, nullable=False)
    slippage_cost: Mapped[float] = mapped_column(Float, nullable=False)
    net_edge: Mapped[float] = mapped_column(Float, nullable=False)
    net_edge_bps: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fee_tier_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimal_trade_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimal_net_edge: Mapped[float | None] = mapped_column(Float, nullable=True)


class V3PoolSnapshotRow(Base):
    """One Uniswap V3 pool observation per tick. Big integers are decimal strings."""

    __tablename__ = "v3_pool_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    pool_address: Mapped[str] = mapped_column(String(42), nullable=False)
    fee_pips: Mapped[int] = mapped_column(Integer, nullable=False)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sqrt_price_x96: Mapped[str] = mapped_column(String(80), nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    liquidity: Mapped[str] = mapped_column(String(80), nullable=False)
    fee_growth_global0_x128: Mapped[str] = mapped_column(String(80), nullable=False)
    fee_growth_global1_x128: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    base_fee_wei: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_incomplete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LpPositionStateRow(Base):
    """LP book state per tick: queryable headline columns plus the full JSON state."""

    __tablename__ = "lp_position_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    net_pnl_quote: Mapped[float] = mapped_column(Float, nullable=False)
    fees_quote: Mapped[float] = mapped_column(Float, nullable=False)
    il_quote: Mapped[float] = mapped_column(Float, nullable=False)
    value_quote: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LpRebalanceRow(Base):
    __tablename__ = "lp_rebalances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArbSimulationRow(Base):
    """One flash-arb simulation per tick: queryable headline columns plus the full JSON."""

    __tablename__ = "arb_simulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    sim_net_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimate_error_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class MempoolSwapRow(Base):
    """One watched (or replayed) swap; updated in place as its status changes."""

    __tablename__ = "mempool_swaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False, unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(24), nullable=False)
    opportunity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    included_block: Mapped[int | None] = mapped_column(Integer, nullable=True)
    math_error_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class MempoolBlockRow(Base):
    """Per-block visibility and verification counts, per mode."""

    __tablename__ = "mempool_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    pool_swap_txs: Mapped[int] = mapped_column(Integer, nullable=False)
    seen_pending: Mapped[int] = mapped_column(Integer, nullable=False)
    backruns: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class MempoolModeChangeRow(Base):
    __tablename__ = "mempool_mode_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    from_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    to_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class TestnetSessionRow(Base):
    """One continuous testnet trader session (specs/005 FR-016)."""

    __tablename__ = "testnet_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class TestnetOrderRow(Base):
    """One testnet order with its life cycle; updated in place."""

    __tablename__ = "testnet_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class TestnetFillRow(Base):
    __tablename__ = "testnet_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    session_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class TestnetShadowFillRow(Base):
    __tablename__ = "testnet_shadow_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    fill_model: Mapped[str] = mapped_column(String(24), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    tick_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class TestnetTickRow(Base):
    __tablename__ = "testnet_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


for _testnet_row in (
    TestnetSessionRow,
    TestnetOrderRow,
    TestnetFillRow,
    TestnetShadowFillRow,
    TestnetTickRow,
):
    _testnet_row.__test__ = False  # not pytest test classes


def add_missing_nullable_columns(engine: Engine) -> list[str]:
    """Idempotently add nullable columns that exist in the models but not in the DB.

    The project uses ``create_all`` without migrations; this keeps databases
    created before a nullable column was added working. Returns added columns.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or not column.nullable:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {column_type}')
                )
                added.append(f"{table.name}.{column.name}")
    return added


def create_tables(db_url: str) -> None:
    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    add_missing_nullable_columns(engine)
    engine.dispose()


def _opportunity_row(opportunity: Opportunity, tick_id: str) -> OpportunityRow:
    return OpportunityRow(
        tick_id=tick_id,
        direction=opportunity.direction.value,
        cex_mid=opportunity.cex_mid,
        amm_price=opportunity.amm_price,
        trial_trade_size=opportunity.trial_trade_size,
        gross_edge=opportunity.gross_edge,
        cex_fee=opportunity.cex_fee,
        amm_fee=opportunity.amm_fee,
        gas_cost=opportunity.gas_cost,
        slippage_cost=opportunity.slippage_cost,
        net_edge=opportunity.net_edge,
        net_edge_bps=opportunity.net_edge_bps,
        timestamp=opportunity.timestamp,
        venue=opportunity.venue,
        fee_tier_bps=opportunity.fee_tier_bps,
        optimal_trade_size=opportunity.optimal_trade_size,
        optimal_net_edge=opportunity.optimal_net_edge,
    )


def _opportunity_from_row(row: OpportunityRow) -> Opportunity:
    return Opportunity(
        direction=ArbitrageDirection(row.direction),
        cex_mid=row.cex_mid,
        amm_price=row.amm_price,
        trial_trade_size=row.trial_trade_size,
        gross_edge=row.gross_edge,
        cex_fee=row.cex_fee,
        amm_fee=row.amm_fee,
        gas_cost=row.gas_cost,
        slippage_cost=row.slippage_cost,
        net_edge=row.net_edge,
        net_edge_bps=row.net_edge_bps,
        timestamp=row.timestamp,
        tick_id=row.tick_id,
        venue=row.venue or "uniswap_v2",
        fee_tier_bps=row.fee_tier_bps,
        optimal_trade_size=row.optimal_trade_size,
        optimal_net_edge=row.optimal_net_edge,
    )


def _v3_snapshot_row(snapshot: V3PoolSnapshot, tick_id: str) -> V3PoolSnapshotRow:
    return V3PoolSnapshotRow(
        tick_id=tick_id,
        pool_address=snapshot.metadata.pool_address,
        fee_pips=snapshot.metadata.fee_pips,
        block_number=snapshot.block_number,
        block_timestamp=snapshot.block_timestamp,
        sqrt_price_x96=str(snapshot.sqrt_price_x96),
        tick=snapshot.tick,
        liquidity=str(snapshot.liquidity),
        fee_growth_global0_x128=str(snapshot.fee_growth_global0_x128),
        fee_growth_global1_x128=str(snapshot.fee_growth_global1_x128),
        price=snapshot.price,
        base_fee_wei=None if snapshot.base_fee_wei is None else str(snapshot.base_fee_wei),
        is_stale=snapshot.is_stale,
        is_incomplete=snapshot.is_incomplete,
        data_source=snapshot.data_source,
        timestamp=snapshot.block_timestamp,
    )


def _v3_row_to_dict(row: V3PoolSnapshotRow) -> dict:
    return {
        "tick_id": row.tick_id,
        "pool_address": row.pool_address,
        "fee_pips": row.fee_pips,
        "block_number": row.block_number,
        "block_timestamp": row.block_timestamp.isoformat(),
        "sqrt_price_x96": row.sqrt_price_x96,
        "tick": row.tick,
        "liquidity": row.liquidity,
        "fee_growth_global0_x128": row.fee_growth_global0_x128,
        "fee_growth_global1_x128": row.fee_growth_global1_x128,
        "price": row.price,
        "base_fee_wei": row.base_fee_wei or "",
        "is_stale": "true" if row.is_stale else "false",
        "is_incomplete": row.is_incomplete,
        "data_source": row.data_source,
    }


def _arb_simulation_row(result: SimulationResult, tick_id: str) -> ArbSimulationRow:
    payload = simulation_to_dict(result)
    payload["tick_id"] = tick_id
    return ArbSimulationRow(
        tick_id=tick_id,
        block_number=result.block_number,
        direction=result.direction,
        outcome=result.outcome,
        sim_net_quote=result.sim_net_quote,
        estimate_error_bps=result.estimate_error_bps,
        payload=json.dumps(payload),
        timestamp=result.timestamp,
    )


def _best_price(levels: tuple[OrderBookLevel, ...]) -> float:
    if not levels:
        return 0.0
    return levels[0].price


def _mid_and_spread_bps(snapshot: OrderBookSnapshot) -> tuple[float, float]:
    best_bid = _best_price(snapshot.bids)
    best_ask = _best_price(snapshot.asks)
    if best_bid <= 0 or best_ask <= 0:
        return 0.0, 0.0
    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0
    return mid, spread_bps


class Repository:
    def __init__(self, db_url: str) -> None:
        self._engine = create_db_engine(db_url)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self._engine)
        add_missing_nullable_columns(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def save_orderbook_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        *,
        tick_id: str | None = None,
    ) -> None:
        best_bid = _best_price(snapshot.bids)
        best_ask = _best_price(snapshot.asks)
        mid, spread_bps = _mid_and_spread_bps(snapshot)
        row = OrderBookSnapshotRow(
            tick_id=tick_id or new_tick_id(),
            symbol=snapshot.symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            spread_bps=spread_bps,
            is_stale=snapshot.is_stale,
            timestamp=snapshot.timestamp,
        )
        with self._session() as session:
            session.add(row)
            session.commit()

    def save_quotes(self, quotes: list[Quote], *, tick_id: str | None = None) -> None:
        resolved_tick_id = tick_id or new_tick_id()
        rows = []
        for quote in quotes:
            if quote.quote_id is None:
                raise ValueError("quote_id is required to persist a quote")
            rows.append(
                QuoteRow(
                    tick_id=resolved_tick_id,
                    quote_id=quote.quote_id,
                    symbol=quote.symbol,
                    side=quote.side.value,
                    price=quote.price,
                    size=quote.size,
                    timestamp=quote.timestamp,
                )
            )
        with self._session() as session:
            session.add_all(rows)
            session.commit()

    def get_quote_by_id(self, quote_id: str) -> Quote | None:
        with self._session() as session:
            row = session.query(QuoteRow).filter(QuoteRow.quote_id == quote_id).one_or_none()
            if row is None:
                return None
            return Quote(
                symbol=row.symbol,
                side=QuoteSide(row.side),
                price=row.price,
                size=row.size,
                timestamp=row.timestamp,
                quote_id=row.quote_id,
                tick_id=row.tick_id,
            )

    def save_fills(self, fills: list[Fill], *, tick_id: str | None = None) -> None:
        resolved_tick_id = tick_id or new_tick_id()
        rows = [
            FillRow(
                tick_id=resolved_tick_id,
                quote_id=fill.quote_id,
                symbol=fill.symbol,
                side=fill.side.value,
                price=fill.price,
                size=fill.size,
                fee=fill.fee,
                timestamp=fill.timestamp,
            )
            for fill in fills
        ]
        with self._session() as session:
            session.add_all(rows)
            session.commit()

    def save_position(self, position: Position, *, tick_id: str | None = None) -> None:
        row = PositionRow(
            tick_id=tick_id or new_tick_id(),
            symbol=position.symbol,
            base_amount=position.base_amount,
            quote_amount=position.quote_amount,
            average_entry_price=position.average_entry_price,
            timestamp=position.timestamp,
        )
        with self._session() as session:
            session.add(row)
            session.commit()

    def save_pnl_snapshot(self, pnl: PnLSnapshot, *, tick_id: str | None = None) -> None:
        row = PnLSnapshotRow(
            tick_id=tick_id or new_tick_id(),
            symbol=pnl.symbol,
            realized_pnl=pnl.realized_pnl,
            unrealized_pnl=pnl.unrealized_pnl,
            total_fees=pnl.total_fees,
            total_pnl=pnl.total_pnl,
            timestamp=pnl.timestamp,
        )
        with self._session() as session:
            session.add(row)
            session.commit()

    def persist_tick(
        self,
        *,
        tick_id: str,
        snapshot: OrderBookSnapshot,
        fills: list[Fill],
        quotes: list[Quote],
        position: Position,
        pnl: PnLSnapshot,
        opportunities: list[Opportunity] | None = None,
        v3_snapshot: V3PoolSnapshot | None = None,
        lp_state: LpPositionState | None = None,
        lp_rebalances: list[RebalanceEvent] | None = None,
        arb_simulation: SimulationResult | None = None,
    ) -> None:
        """Persist all tick artifacts in a single database transaction."""
        with self._session() as session:
            best_bid = _best_price(snapshot.bids)
            best_ask = _best_price(snapshot.asks)
            mid, spread_bps = _mid_and_spread_bps(snapshot)
            session.add(
                OrderBookSnapshotRow(
                    tick_id=tick_id,
                    symbol=snapshot.symbol,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid_price=mid,
                    spread_bps=spread_bps,
                    is_stale=snapshot.is_stale,
                    timestamp=snapshot.timestamp,
                )
            )
            for fill in fills:
                session.add(
                    FillRow(
                        tick_id=tick_id,
                        quote_id=fill.quote_id,
                        symbol=fill.symbol,
                        side=fill.side.value,
                        price=fill.price,
                        size=fill.size,
                        fee=fill.fee,
                        timestamp=fill.timestamp,
                    )
                )
            for quote in quotes:
                if quote.quote_id is None:
                    raise ValueError("quote_id is required to persist a quote")
                session.add(
                    QuoteRow(
                        tick_id=tick_id,
                        quote_id=quote.quote_id,
                        symbol=quote.symbol,
                        side=quote.side.value,
                        price=quote.price,
                        size=quote.size,
                        timestamp=quote.timestamp,
                    )
                )
            session.add(
                PositionRow(
                    tick_id=tick_id,
                    symbol=position.symbol,
                    base_amount=position.base_amount,
                    quote_amount=position.quote_amount,
                    average_entry_price=position.average_entry_price,
                    timestamp=position.timestamp,
                )
            )
            session.add(
                PnLSnapshotRow(
                    tick_id=tick_id,
                    symbol=pnl.symbol,
                    realized_pnl=pnl.realized_pnl,
                    unrealized_pnl=pnl.unrealized_pnl,
                    total_fees=pnl.total_fees,
                    total_pnl=pnl.total_pnl,
                    timestamp=pnl.timestamp,
                )
            )
            for opportunity in opportunities or []:
                session.add(_opportunity_row(opportunity, tick_id))
            if v3_snapshot is not None:
                session.add(_v3_snapshot_row(v3_snapshot, tick_id))
            if lp_state is not None:
                session.add(
                    LpPositionStateRow(
                        tick_id=tick_id,
                        status=lp_state.status,
                        net_pnl_quote=lp_state.net_pnl_quote,
                        fees_quote=lp_state.fees_quote,
                        il_quote=lp_state.il_quote,
                        value_quote=lp_state.value_quote,
                        payload=json.dumps(lp_state_to_dict(lp_state)),
                        timestamp=lp_state.timestamp,
                    )
                )
            for event in lp_rebalances or []:
                session.add(
                    LpRebalanceRow(
                        tick_id=tick_id,
                        reason=event.reason,
                        payload=json.dumps(rebalance_to_dict(event)),
                        timestamp=event.timestamp,
                    )
                )
            if arb_simulation is not None:
                session.add(_arb_simulation_row(arb_simulation, tick_id))
            session.commit()

    def save_opportunities(
        self,
        opportunities: list[Opportunity],
        *,
        tick_id: str | None = None,
    ) -> None:
        resolved_tick_id = tick_id or new_tick_id()
        rows = [_opportunity_row(opportunity, resolved_tick_id) for opportunity in opportunities]
        with self._session() as session:
            session.add_all(rows)
            session.commit()

    def get_latest_opportunities(self, limit: int = 10) -> list[Opportunity]:
        with self._session() as session:
            rows = (
                session.query(OpportunityRow).order_by(OpportunityRow.id.desc()).limit(limit).all()
            )
            return [_opportunity_from_row(row) for row in rows]

    def get_latest_fills(self, limit: int = 20, symbol: str | None = None) -> list[Fill]:
        with self._session() as session:
            query = session.query(FillRow)
            if symbol is not None:
                query = query.filter(FillRow.symbol == symbol)
            rows = query.order_by(FillRow.id.desc()).limit(limit).all()
        return [
            Fill(
                symbol=row.symbol,
                side=QuoteSide(row.side),
                price=row.price,
                size=row.size,
                fee=row.fee,
                timestamp=row.timestamp,
                quote_id=row.quote_id,
                tick_id=row.tick_id,
            )
            for row in rows
        ]

    def get_pnl_history(self, limit: int = 200, symbol: str | None = None) -> list[PnLSnapshot]:
        with self._session() as session:
            query = session.query(PnLSnapshotRow)
            if symbol is not None:
                query = query.filter(PnLSnapshotRow.symbol == symbol)
            rows = query.order_by(PnLSnapshotRow.id.desc()).limit(limit).all()
        return [
            PnLSnapshot(
                symbol=row.symbol,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                total_fees=row.total_fees,
                total_pnl=row.total_pnl,
                timestamp=row.timestamp,
                tick_id=row.tick_id,
            )
            for row in reversed(rows)
        ]

    def get_tick_audit(self, tick_id: str) -> TickAuditBundle | None:
        with self._session() as session:
            snapshot_rows = (
                session.query(OrderBookSnapshotRow)
                .filter(OrderBookSnapshotRow.tick_id == tick_id)
                .all()
            )
            quote_rows = session.query(QuoteRow).filter(QuoteRow.tick_id == tick_id).all()
            fill_rows = session.query(FillRow).filter(FillRow.tick_id == tick_id).all()
            position_rows = session.query(PositionRow).filter(PositionRow.tick_id == tick_id).all()
            pnl_rows = session.query(PnLSnapshotRow).filter(PnLSnapshotRow.tick_id == tick_id).all()
            opportunity_rows = (
                session.query(OpportunityRow).filter(OpportunityRow.tick_id == tick_id).all()
            )
            v3_rows = (
                session.query(V3PoolSnapshotRow).filter(V3PoolSnapshotRow.tick_id == tick_id).all()
            )
            lp_rows = (
                session.query(LpPositionStateRow)
                .filter(LpPositionStateRow.tick_id == tick_id)
                .all()
            )
            rebalance_rows = (
                session.query(LpRebalanceRow).filter(LpRebalanceRow.tick_id == tick_id).all()
            )
            arb_rows = (
                session.query(ArbSimulationRow).filter(ArbSimulationRow.tick_id == tick_id).all()
            )

        if not any(
            [
                snapshot_rows,
                quote_rows,
                fill_rows,
                position_rows,
                pnl_rows,
                opportunity_rows,
                v3_rows,
                lp_rows,
                arb_rows,
            ]
        ):
            return None

        quotes = [
            Quote(
                symbol=row.symbol,
                side=QuoteSide(row.side),
                price=row.price,
                size=row.size,
                timestamp=row.timestamp,
                quote_id=row.quote_id,
                tick_id=row.tick_id,
            )
            for row in quote_rows
        ]
        fills = [
            Fill(
                symbol=row.symbol,
                side=QuoteSide(row.side),
                price=row.price,
                size=row.size,
                fee=row.fee,
                timestamp=row.timestamp,
                quote_id=row.quote_id,
                tick_id=row.tick_id,
            )
            for row in fill_rows
        ]
        positions = [
            Position(
                symbol=row.symbol,
                base_amount=row.base_amount,
                quote_amount=row.quote_amount,
                average_entry_price=row.average_entry_price,
                timestamp=row.timestamp,
                tick_id=row.tick_id,
            )
            for row in position_rows
        ]
        pnl_snapshots = [
            PnLSnapshot(
                symbol=row.symbol,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                total_fees=row.total_fees,
                total_pnl=row.total_pnl,
                timestamp=row.timestamp,
                tick_id=row.tick_id,
            )
            for row in pnl_rows
        ]
        opportunities = [_opportunity_from_row(row) for row in opportunity_rows]

        return TickAuditBundle(
            tick_id=tick_id,
            orderbook_snapshots=[
                {
                    "tick_id": row.tick_id,
                    "symbol": row.symbol,
                    "best_bid": row.best_bid,
                    "best_ask": row.best_ask,
                    "mid_price": row.mid_price,
                    "spread_bps": row.spread_bps,
                    "is_stale": row.is_stale,
                    "timestamp": row.timestamp.isoformat(),
                }
                for row in snapshot_rows
            ],
            quotes=[quote_to_dict(quote) for quote in quotes],
            fills=[fill_to_dict(fill) for fill in fills],
            positions=[position_to_dict(position) for position in positions],
            pnl_snapshots=[pnl_history_point(pnl) for pnl in pnl_snapshots],
            opportunities=[opportunity_to_dict(opportunity) for opportunity in opportunities],
            v3_pool_snapshots=[_v3_row_to_dict(row) for row in v3_rows],
            lp_positions=[json.loads(row.payload) for row in lp_rows],
            lp_rebalances=[json.loads(row.payload) for row in rebalance_rows],
            arb_simulations=[json.loads(row.payload) for row in arb_rows],
        )

    def load_orderbook_snapshots(
        self,
        *,
        symbol: str,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
        limit: int | None = None,
    ) -> list[OrderBookSnapshot]:
        with self._session() as session:
            query = session.query(OrderBookSnapshotRow).filter(
                OrderBookSnapshotRow.symbol == symbol
            )
            if from_timestamp is not None:
                query = query.filter(OrderBookSnapshotRow.timestamp >= from_timestamp)
            if to_timestamp is not None:
                query = query.filter(OrderBookSnapshotRow.timestamp <= to_timestamp)
            query = query.order_by(OrderBookSnapshotRow.timestamp.asc())
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()

        snapshots: list[OrderBookSnapshot] = []
        for row in rows:
            spread = row.best_ask - row.best_bid
            bids = (
                OrderBookLevel(row.best_bid, 1.0),
                OrderBookLevel(row.best_bid - spread * 0.1, 1.0),
            )
            asks = (
                OrderBookLevel(row.best_ask, 1.0),
                OrderBookLevel(row.best_ask + spread * 0.1, 1.0),
            )
            snapshots.append(
                OrderBookSnapshot(
                    symbol=row.symbol,
                    bids=bids,
                    asks=asks,
                    timestamp=row.timestamp,
                    is_stale=row.is_stale,
                )
            )
        return snapshots

    def get_arb_simulations(self, limit: int = 100) -> list[SimulationResult]:
        """Latest simulations, newest first."""
        with self._session() as session:
            rows = (
                session.query(ArbSimulationRow)
                .order_by(ArbSimulationRow.id.desc())
                .limit(limit)
                .all()
            )
        return [simulation_from_dict(json.loads(row.payload)) for row in rows]

    def get_latest_lp_state(self) -> LpPositionState | None:
        with self._session() as session:
            row = session.query(LpPositionStateRow).order_by(LpPositionStateRow.id.desc()).first()
        return lp_state_from_dict(json.loads(row.payload)) if row else None

    def get_lp_history(self, limit: int = 200) -> list[LpPositionState]:
        with self._session() as session:
            rows = (
                session.query(LpPositionStateRow)
                .order_by(LpPositionStateRow.id.desc())
                .limit(limit)
                .all()
            )
        return [lp_state_from_dict(json.loads(row.payload)) for row in reversed(rows)]

    def get_lp_rebalances(self, limit: int = 20) -> list[RebalanceEvent]:
        with self._session() as session:
            rows = (
                session.query(LpRebalanceRow).order_by(LpRebalanceRow.id.desc()).limit(limit).all()
            )
        return [rebalance_from_dict(json.loads(row.payload)) for row in rows]

    def get_lp_state_window(
        self, from_timestamp: datetime | None, to_timestamp: datetime | None
    ) -> tuple[LpPositionState | None, LpPositionState | None]:
        """First and last LP state inside [from, to]."""
        with self._session() as session:
            query = session.query(LpPositionStateRow)
            if from_timestamp is not None:
                query = query.filter(LpPositionStateRow.timestamp >= from_timestamp)
            if to_timestamp is not None:
                query = query.filter(LpPositionStateRow.timestamp <= to_timestamp)
            first = query.order_by(LpPositionStateRow.id.asc()).first()
            last = query.order_by(LpPositionStateRow.id.desc()).first()
        return (
            lp_state_from_dict(json.loads(first.payload)) if first else None,
            lp_state_from_dict(json.loads(last.payload)) if last else None,
        )

    def get_pnl_window(
        self, from_timestamp: datetime | None, to_timestamp: datetime | None
    ) -> tuple[PnLSnapshot | None, PnLSnapshot | None]:
        """First and last CEX PnL snapshot inside [from, to]."""
        with self._session() as session:
            query = session.query(PnLSnapshotRow)
            if from_timestamp is not None:
                query = query.filter(PnLSnapshotRow.timestamp >= from_timestamp)
            if to_timestamp is not None:
                query = query.filter(PnLSnapshotRow.timestamp <= to_timestamp)
            rows = [
                query.order_by(PnLSnapshotRow.id.asc()).first(),
                query.order_by(PnLSnapshotRow.id.desc()).first(),
            ]
        snapshots = [
            PnLSnapshot(
                symbol=row.symbol,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                total_fees=row.total_fees,
                total_pnl=row.total_pnl,
                timestamp=row.timestamp,
                tick_id=row.tick_id,
            )
            if row
            else None
            for row in rows
        ]
        return snapshots[0], snapshots[1]

    def load_v3_observations(
        self,
        *,
        pool_address: str | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Persisted live V3 snapshots as observation rows (same shape as the CSV fixture)."""
        with self._session() as session:
            query = session.query(V3PoolSnapshotRow).filter(V3PoolSnapshotRow.data_source == "live")
            if pool_address is not None:
                query = query.filter(V3PoolSnapshotRow.pool_address == pool_address)
            if from_timestamp is not None:
                query = query.filter(V3PoolSnapshotRow.block_timestamp >= from_timestamp)
            if to_timestamp is not None:
                query = query.filter(V3PoolSnapshotRow.block_timestamp <= to_timestamp)
            query = query.order_by(V3PoolSnapshotRow.id.asc())
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()
        observations: list[dict] = []
        last_block = None
        for row in rows:
            if row.block_number == last_block:
                continue  # several loop ticks can land in the same block
            last_block = row.block_number
            observations.append(_v3_row_to_dict(row))
        return observations

    # --- mempool watcher (specs/004-mempool-watcher) -----------------------------

    def upsert_mempool_swaps(self, swaps: list[WatchedSwap]) -> None:
        if not swaps:
            return
        with self._session() as session:
            for swap in swaps:
                row = (
                    session.query(MempoolSwapRow)
                    .filter(MempoolSwapRow.tx_hash == swap.tx_hash)
                    .one_or_none()
                )
                if row is None:
                    row = MempoolSwapRow(tx_hash=swap.tx_hash)
                    session.add(row)
                outcome = swap.outcome
                row.mode = swap.mode
                row.status = swap.status
                row.route = swap.route
                row.opportunity = swap.opportunity
                row.first_seen_at = swap.first_seen_at
                row.included_block = outcome.block_number if outcome is not None else None
                row.math_error_bps = (
                    max((leg.math_error_bps for leg in outcome.legs), default=None)
                    if outcome is not None
                    else None
                )
                row.payload = json.dumps(watched_to_dict(swap))
            session.commit()

    def save_mempool_block(self, block: BlockVisibility) -> None:
        with self._session() as session:
            row = (
                session.query(MempoolBlockRow)
                .filter(
                    MempoolBlockRow.block_hash == block.block_hash,
                    MempoolBlockRow.mode == block.mode,
                )
                .one_or_none()
            )
            if row is None:
                row = MempoolBlockRow(block_hash=block.block_hash, mode=block.mode)
                session.add(row)
            row.block_number = block.block_number
            row.pool_swap_txs = block.pool_swap_txs
            row.seen_pending = block.seen_pending
            row.backruns = block.backruns
            row.timestamp = block.timestamp
            row.payload = json.dumps(block_to_dict(block))
            session.commit()

    def save_mempool_mode_change(self, change: ModeChange) -> None:
        with self._session() as session:
            session.add(
                MempoolModeChangeRow(
                    at=change.at,
                    from_mode=change.from_mode,
                    to_mode=change.to_mode,
                    source=change.source,
                    reason=change.reason,
                )
            )
            session.commit()

    def get_mempool_swaps(
        self, limit: int = 50, *, mode: str | None = None, status: str | None = None
    ) -> list[WatchedSwap]:
        with self._session() as session:
            query = session.query(MempoolSwapRow)
            if mode is not None:
                query = query.filter(MempoolSwapRow.mode == mode)
            if status is not None:
                query = query.filter(MempoolSwapRow.status == status)
            rows = (
                query.order_by(MempoolSwapRow.first_seen_at.desc(), MempoolSwapRow.id.desc())
                .limit(limit)
                .all()
            )
        return [watched_from_dict(json.loads(row.payload)) for row in rows]

    def get_mempool_swap(self, tx_hash: str) -> WatchedSwap | None:
        with self._session() as session:
            row = (
                session.query(MempoolSwapRow)
                .filter(MempoolSwapRow.tx_hash == tx_hash.lower())
                .one_or_none()
            )
        return watched_from_dict(json.loads(row.payload)) if row is not None else None

    def get_mempool_swaps_since(self, since: datetime) -> list[WatchedSwap]:
        with self._session() as session:
            rows = (
                session.query(MempoolSwapRow)
                .filter(MempoolSwapRow.first_seen_at >= since)
                .order_by(MempoolSwapRow.first_seen_at)
                .all()
            )
        return [watched_from_dict(json.loads(row.payload)) for row in rows]

    def get_mempool_blocks(
        self, limit: int = 50, *, mode: str | None = None
    ) -> list[BlockVisibility]:
        with self._session() as session:
            query = session.query(MempoolBlockRow)
            if mode is not None:
                query = query.filter(MempoolBlockRow.mode == mode)
            rows = (
                query.order_by(MempoolBlockRow.block_number.desc(), MempoolBlockRow.id.desc())
                .limit(limit)
                .all()
            )
        return [block_from_dict(json.loads(row.payload)) for row in rows]

    def get_mempool_blocks_since(self, since: datetime) -> list[BlockVisibility]:
        with self._session() as session:
            rows = (
                session.query(MempoolBlockRow)
                .filter(MempoolBlockRow.timestamp >= since)
                .order_by(MempoolBlockRow.block_number)
                .all()
            )
        return [block_from_dict(json.loads(row.payload)) for row in rows]

    def get_mempool_mode_changes(self, limit: int = 20) -> list[dict]:
        with self._session() as session:
            rows = (
                session.query(MempoolModeChangeRow)
                .order_by(MempoolModeChangeRow.id.desc())
                .limit(limit)
                .all()
            )
        return [
            mode_change_to_dict(
                ModeChange(row.at, row.from_mode, row.to_mode, row.source, row.reason)
            )
            for row in rows
        ]

    def purge_mempool(self, before: datetime) -> int:
        """Retention sweep (FR-025): delete records older than ``before``."""
        with self._session() as session:
            removed = (
                session.query(MempoolSwapRow)
                .filter(MempoolSwapRow.first_seen_at < before)
                .delete(synchronize_session=False)
            )
            removed += (
                session.query(MempoolBlockRow)
                .filter(MempoolBlockRow.timestamp < before)
                .delete(synchronize_session=False)
            )
            removed += (
                session.query(MempoolModeChangeRow)
                .filter(MempoolModeChangeRow.at < before)
                .delete(synchronize_session=False)
            )
            session.commit()
        return removed

    # --- testnet trader (specs/005-binance-testnet-execution) -----------------------

    def save_testnet_session(self, item: TestnetSession) -> None:
        with self._session() as session:
            row = (
                session.query(TestnetSessionRow)
                .filter(TestnetSessionRow.session_id == item.session_id)
                .one_or_none()
            )
            if row is None:
                row = TestnetSessionRow(session_id=item.session_id)
                session.add(row)
            row.started_at = item.started_at
            row.ended_at = item.ended_at
            row.payload = json.dumps(session_to_dict(item))
            session.commit()

    def get_testnet_sessions(self, limit: int = 20) -> list[TestnetSession]:
        with self._session() as session:
            rows = (
                session.query(TestnetSessionRow)
                .order_by(TestnetSessionRow.started_at.desc(), TestnetSessionRow.id.desc())
                .limit(limit)
                .all()
            )
        return [session_from_dict(json.loads(row.payload)) for row in rows]

    def upsert_testnet_orders(self, orders: list[TestnetOrder]) -> None:
        if not orders:
            return
        with self._session() as session:
            for order in orders:
                row = (
                    session.query(TestnetOrderRow)
                    .filter(TestnetOrderRow.client_order_id == order.client_order_id)
                    .one_or_none()
                )
                if row is None:
                    row = TestnetOrderRow(client_order_id=order.client_order_id)
                    session.add(row)
                row.session_id = order.session_id
                row.state = order.state
                row.placed_at = order.placed_at
                row.payload = json.dumps(order_to_dict(order))
            session.commit()

    def get_testnet_orders(
        self,
        *,
        session_id: str | None = None,
        states: frozenset[str] | set[str] | None = None,
        limit: int | None = 50,
    ) -> list[TestnetOrder]:
        with self._session() as session:
            query = session.query(TestnetOrderRow)
            if session_id is not None:
                query = query.filter(TestnetOrderRow.session_id == session_id)
            if states is not None:
                query = query.filter(TestnetOrderRow.state.in_(sorted(states)))
            query = query.order_by(TestnetOrderRow.placed_at.desc(), TestnetOrderRow.id.desc())
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()
        return [order_from_dict(json.loads(row.payload)) for row in rows]

    def get_testnet_order(self, client_order_id: str) -> TestnetOrder | None:
        with self._session() as session:
            row = (
                session.query(TestnetOrderRow)
                .filter(TestnetOrderRow.client_order_id == client_order_id)
                .one_or_none()
            )
        return order_from_dict(json.loads(row.payload)) if row is not None else None

    def save_testnet_fills(self, fills: list[TestnetFill]) -> None:
        if not fills:
            return
        with self._session() as session:
            for fill in fills:
                exists = (
                    session.query(TestnetFillRow.id)
                    .filter(TestnetFillRow.trade_id == fill.trade_id)
                    .first()
                )
                if exists is None:
                    session.add(
                        TestnetFillRow(
                            trade_id=fill.trade_id,
                            session_id=fill.session_id,
                            at=fill.at,
                            payload=json.dumps(testnet_fill_to_dict(fill)),
                        )
                    )
            session.commit()

    def get_testnet_fills(
        self, *, session_id: str | None = None, limit: int | None = 50
    ) -> list[TestnetFill]:
        with self._session() as session:
            query = session.query(TestnetFillRow)
            if session_id is not None:
                query = query.filter(TestnetFillRow.session_id == session_id)
            query = query.order_by(TestnetFillRow.at.desc(), TestnetFillRow.id.desc())
            if limit is not None:
                query = query.limit(limit)
            rows = query.all()
        return [testnet_fill_from_dict(json.loads(row.payload)) for row in rows]

    def save_testnet_shadow_fills(self, fills: list[ShadowFill]) -> None:
        if not fills:
            return
        with self._session() as session:
            for fill in fills:
                session.add(
                    TestnetShadowFillRow(
                        session_id=fill.session_id,
                        fill_model=fill.fill_model,
                        client_order_id=fill.client_order_id,
                        tick_seq=fill.tick_seq,
                        payload=json.dumps(shadow_to_dict(fill)),
                    )
                )
            session.commit()

    def get_testnet_shadow_fills(self, session_id: str) -> list[ShadowFill]:
        with self._session() as session:
            rows = (
                session.query(TestnetShadowFillRow)
                .filter(TestnetShadowFillRow.session_id == session_id)
                .order_by(TestnetShadowFillRow.tick_seq, TestnetShadowFillRow.id)
                .all()
            )
        return [shadow_from_dict(json.loads(row.payload)) for row in rows]

    def save_testnet_tick(self, tick: TickRecord) -> None:
        with self._session() as session:
            session.add(
                TestnetTickRow(
                    session_id=tick.session_id,
                    seq=tick.seq,
                    at=tick.at,
                    payload=json.dumps(tick_to_dict(tick)),
                )
            )
            session.commit()

    def get_testnet_ticks(self, session_id: str) -> list[TickRecord]:
        with self._session() as session:
            rows = (
                session.query(TestnetTickRow)
                .filter(TestnetTickRow.session_id == session_id)
                .order_by(TestnetTickRow.seq)
                .all()
            )
        return [tick_from_dict(json.loads(row.payload)) for row in rows]

    def _session(self) -> Session:
        return self._session_factory()
