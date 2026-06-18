from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.models.domain import (
    Fill,
    OrderBookLevel,
    OrderBookSnapshot,
    PnLSnapshot,
    Position,
    Quote,
)


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


def create_tables(db_url: str) -> None:
    engine = create_engine(db_url)
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
        self._engine = create_engine(db_url)
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
        rows = [
            QuoteRow(
                quote_id=f"{quote.symbol}-{quote.side.value}-{quote.timestamp.isoformat()}",
                symbol=quote.symbol,
                side=quote.side.value,
                price=quote.price,
                size=quote.size,
                timestamp=quote.timestamp,
            )
            for quote in quotes
        ]
        with self._session() as session:
            session.add_all(rows)
            session.commit()

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

    def _session(self) -> Session:
        return self._session_factory()
