from datetime import timedelta

import pytest

from app.execution.fill_model import OpenQuote
from app.execution.paper_broker import PaperBroker
from app.execution.trade_fill import detect_trade_fills
from app.models.domain import Quote, QuoteSide
from app.simlab.recording import TradePrint
from tests.simlab_helpers import T0


def _quote(side: QuoteSide, price: float, size: float = 1.0, at=T0) -> OpenQuote:
    quote = Quote("BTC/USDT", side, price, size, at, quote_id=f"{side.value}-{price}")
    return OpenQuote(quote_id=quote.quote_id, quote=quote)


def _trade(seconds: float, price: float, amount: float) -> TradePrint:
    return TradePrint(T0 + timedelta(seconds=seconds), price, amount)


def test_a_trade_through_the_bid_fills_it() -> None:
    fills = detect_trade_fills([_quote(QuoteSide.BID, 100.0)], [_trade(0.5, 99.9, 0.4)], 2.0, at=T0)
    assert len(fills) == 1
    assert fills[0].size == 0.4 and fills[0].price == 100.0
    assert abs(fills[0].fee - 100.0 * 0.4 * 0.0002) < 1e-12


def test_a_trade_at_the_ask_fills_and_above_the_bid_does_not() -> None:
    quotes = [_quote(QuoteSide.BID, 100.0), _quote(QuoteSide.ASK, 101.0)]
    fills = detect_trade_fills(quotes, [_trade(0.5, 101.0, 0.3)], 2.0, at=T0)
    assert [(fill.side, fill.size) for fill in fills] == [(QuoteSide.ASK, 0.3)]


def test_fill_is_capped_by_quote_size_and_trade_size_is_used_up() -> None:
    quotes = [_quote(QuoteSide.BID, 100.0, size=0.5), _quote(QuoteSide.BID, 99.5, size=0.5)]
    fills = detect_trade_fills(quotes, [_trade(0.5, 99.0, 0.7)], 2.0, at=T0)
    assert [fill.size for fill in fills] == pytest.approx([0.5, 0.2])


def test_trades_before_the_quote_existed_do_not_fill() -> None:
    later = _quote(QuoteSide.BID, 100.0, at=T0 + timedelta(seconds=2))
    assert detect_trade_fills([later], [_trade(1.0, 90.0, 5.0)], 2.0, at=T0) == []


def test_several_trades_add_up_to_the_quote() -> None:
    trades = [_trade(0.2, 100.0, 0.3), _trade(0.4, 100.0, 0.3), _trade(0.6, 100.0, 0.9)]
    fills = detect_trade_fills([_quote(QuoteSide.BID, 100.0)], trades, 2.0, at=T0)
    assert len(fills) == 1 and abs(fills[0].size - 1.0) < 1e-12


def test_broker_books_trade_fills_and_keeps_the_remainder() -> None:
    broker = PaperBroker("BTC/USDT", 10_000.0, 2.0)
    broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 1.0, T0, quote_id="b1")])
    fills = broker.apply_trade_fills([_trade(0.5, 100.0, 0.25)], T0 + timedelta(seconds=1))
    assert [fill.quote_id for fill in fills] == ["b1"]
    assert broker.inventory.base_amount == 0.25
    assert broker.open_quotes[0].quote.size == 0.75


def test_broker_keeps_a_callers_quote_id() -> None:
    broker = PaperBroker("BTC/USDT", 10_000.0, 2.0)
    submitted = broker.submit_quotes(
        [Quote("BTC/USDT", QuoteSide.BID, 100.0, 1.0, T0, quote_id="mine")]
    )
    assert submitted[0].quote_id == "mine"
    unnamed = broker.submit_quotes([Quote("BTC/USDT", QuoteSide.BID, 100.0, 1.0, T0)])
    assert unnamed[0].quote_id and unnamed[0].quote_id != "mine"
