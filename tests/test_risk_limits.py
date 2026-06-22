from datetime import UTC, datetime

from app.models.domain import Position, Quote, QuoteSide
from app.risk.limits import filter_quotes_by_position_limit


def _position(base: float, quote: float = 10_000.0) -> Position:
    return Position("BTC/USDT", base, quote, 100.0, datetime.now(UTC))


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
        maker_fee_bps=10.0,
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
        maker_fee_bps=10.0,
    )
    assert approved == []


def test_cash_account_blocks_ask_without_base() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(base=0.0),
        max_position_base=1.0,
        max_position_notional=10_000.0,
        maker_fee_bps=10.0,
    )
    assert len(approved) == 1
    assert approved[0].side == QuoteSide.BID


def test_cash_account_blocks_bid_without_quote_balance() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(base=0.01, quote=0.1),
        max_position_base=1.0,
        max_position_notional=10_000.0,
        maker_fee_bps=10.0,
    )
    assert len(approved) == 1
    assert approved[0].side == QuoteSide.ASK


def test_cash_account_allows_both_sides_when_funded() -> None:
    quotes = _quotes()
    approved = filter_quotes_by_position_limit(
        quotes,
        _position(base=0.01, quote=10_000.0),
        max_position_base=0.02,
        max_position_notional=10_000.0,
        maker_fee_bps=10.0,
    )
    assert len(approved) == 2


def test_cumulative_cash_reserves_quote_across_multiple_bids() -> None:
    now = datetime.now(UTC)
    bids = [
        Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.05, now),
        Quote("BTC/USDT", QuoteSide.BID, 100.0, 0.05, now),
    ]
    approved = filter_quotes_by_position_limit(
        bids,
        _position(base=0.0, quote=10.0),
        max_position_base=1.0,
        max_position_notional=10_000.0,
        maker_fee_bps=10.0,
    )
    assert len(approved) == 1
