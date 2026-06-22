from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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
from app.storage.database import create_db_engine


class Base(DeclarativeBase):
    pass


class OrderBookSnapshotRow(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    quote_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillRow(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_amount: Mapped[float] = mapped_column(Float, nullable=False)
    quote_amount: Mapped[float] = mapped_column(Float, nullable=False)
    average_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PnLSnapshotRow(Base):
    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpportunityRow(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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


def create_tables(db_url: str) -> None:
    engine = create_db_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()


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

    def close(self) -> None:
        self._engine.dispose()

    def save_orderbook_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        best_bid = _best_price(snapshot.bids)
        best_ask = _best_price(snapshot.asks)
        mid, spread_bps = _mid_and_spread_bps(snapshot)
        row = OrderBookSnapshotRow(
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

    def save_quotes(self, quotes: list[Quote]) -> None:
        rows = []
        for quote in quotes:
            if quote.quote_id is None:
                raise ValueError("quote_id is required to persist a quote")
            rows.append(
                QuoteRow(
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
            )

    def save_fills(self, fills: list[Fill]) -> None:
        rows = [
            FillRow(
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

    def save_position(self, position: Position) -> None:
        row = PositionRow(
            symbol=position.symbol,
            base_amount=position.base_amount,
            quote_amount=position.quote_amount,
            average_entry_price=position.average_entry_price,
            timestamp=position.timestamp,
        )
        with self._session() as session:
            session.add(row)
            session.commit()

    def save_pnl_snapshot(self, pnl: PnLSnapshot) -> None:
        row = PnLSnapshotRow(
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
        snapshot: OrderBookSnapshot,
        fills: list[Fill],
        quotes: list[Quote],
        position: Position,
        pnl: PnLSnapshot,
    ) -> None:
        """Persist all tick execution artifacts in a single database transaction."""
        with self._session() as session:
            best_bid = _best_price(snapshot.bids)
            best_ask = _best_price(snapshot.asks)
            mid, spread_bps = _mid_and_spread_bps(snapshot)
            session.add(
                OrderBookSnapshotRow(
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
                    symbol=position.symbol,
                    base_amount=position.base_amount,
                    quote_amount=position.quote_amount,
                    average_entry_price=position.average_entry_price,
                    timestamp=position.timestamp,
                )
            )
            session.add(
                PnLSnapshotRow(
                    symbol=pnl.symbol,
                    realized_pnl=pnl.realized_pnl,
                    unrealized_pnl=pnl.unrealized_pnl,
                    total_fees=pnl.total_fees,
                    total_pnl=pnl.total_pnl,
                    timestamp=pnl.timestamp,
                )
            )
            session.commit()

    def save_opportunities(self, opportunities: list[Opportunity]) -> None:
        rows = [
            OpportunityRow(
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
            )
            for opportunity in opportunities
        ]
        with self._session() as session:
            session.add_all(rows)
            session.commit()

    def get_latest_opportunities(self, limit: int = 10) -> list[Opportunity]:
        with self._session() as session:
            rows = (
                session.query(OpportunityRow)
                .order_by(OpportunityRow.id.desc())
                .limit(limit)
                .all()
            )
            return [
                Opportunity(
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
                )
                for row in rows
            ]

    def get_latest_fills(self, limit: int = 20) -> list[Fill]:
        with self._session() as session:
            rows = (
                session.query(FillRow).order_by(FillRow.id.desc()).limit(limit).all()
            )
        return [
            Fill(
                symbol=row.symbol,
                side=QuoteSide(row.side),
                price=row.price,
                size=row.size,
                fee=row.fee,
                timestamp=row.timestamp,
                quote_id=row.quote_id,
            )
            for row in rows
        ]

    def get_pnl_history(self, limit: int = 200) -> list[PnLSnapshot]:
        with self._session() as session:
            rows = (
                session.query(PnLSnapshotRow)
                .order_by(PnLSnapshotRow.id.desc())
                .limit(limit)
                .all()
            )
        return [
            PnLSnapshot(
                symbol=row.symbol,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                total_fees=row.total_fees,
                total_pnl=row.total_pnl,
                timestamp=row.timestamp,
            )
            for row in reversed(rows)
        ]

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

    def _session(self) -> Session:
        return self._session_factory()
