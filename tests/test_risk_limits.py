from datetime import UTC, datetime

from app.models.domain import Position, Quote, QuoteSide
from app.risk.limits import filter_quotes_by_position_limit


def _position(base: float) -> Position:
    return Position("BTC/USDT", base, 10_000.0, 100.0, datetime.now(UTC))


def _quotes() -> list[Quote]:
    now = datetime.now(UTC)
    return [
        Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.005, now),
        Quote("BTC/USDT", QuoteSide.ASK, 100.0, 0.005, now),
    ]


def test_position_cap_blocks_bid() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(0.009),
        max_position_base=0.01,
        max_position_notional=10_000.0,
    )
    assert len(approved) == 1
    assert approved[0].side == QuoteSide.ASK


def test_notional_cap_blocks_quotes() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(0.0),
        max_position_base=1.0,
        max_position_notional=0.4,
    )
    assert approved == []


def test_all_quotes_pass_within_limits() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(0.0),
        max_position_base=0.01,
        max_position_notional=10_000.0,
    )
    assert len(approved) == 2
