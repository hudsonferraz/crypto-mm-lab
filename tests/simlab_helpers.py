"""Offline fakes for the simulation lab tests (no network)."""

from datetime import UTC, datetime, timedelta

from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.simlab.markets import MarketRules, PublicClients
from app.simlab.recording import Recording, TradePrint

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

RULES = MarketRules(
    exchange="binance",
    symbol="BTC/USDT",
    price_step=0.01,
    amount_step=0.00001,
    min_amount=0.00001,
    min_cost=5.0,
)


def book(mid: float, at: datetime, half_spread: float = 0.5, symbol: str = "BTC/USDT"):
    return OrderBookSnapshot(
        symbol=symbol,
        bids=(OrderBookLevel(mid - half_spread, 1.0), OrderBookLevel(mid - 2 * half_spread, 2.0)),
        asks=(OrderBookLevel(mid + half_spread, 1.0), OrderBookLevel(mid + 2 * half_spread, 2.0)),
        timestamp=at,
    )


def make_recording(
    mids: list[float],
    trades: list[tuple[float, float, float]] | None = None,
    *,
    rules: MarketRules = RULES,
) -> Recording:
    """Snapshots one second apart; trades given as (seconds, price, amount)."""
    snapshots = [book(mid, T0 + timedelta(seconds=index)) for index, mid in enumerate(mids)]
    prints = [
        TradePrint(T0 + timedelta(seconds=seconds), price, amount, None)
        for seconds, price, amount in (trades or [])
    ]
    return Recording(
        exchange=rules.exchange,
        symbol=rules.symbol,
        rules=rules,
        started_at=T0,
        ended_at=T0 + timedelta(seconds=len(mids)),
        source="bundled",
        label="test recording",
        snapshots=snapshots,
        trades=prints,
    )


class FakeClient:
    """Enough of a ccxt client for the catalog and the recorder."""

    def __init__(self, *, markets=None, mid: float = 80_000.0, fail_books: int = 0) -> None:
        self.markets = markets if markets is not None else default_markets()
        self.mid = mid
        self.fail_books = fail_books
        self.book_calls = 0
        self.trade_id = 0
        self.now_ms = int(T0.timestamp() * 1000)

    def load_markets(self, reload=False, params=None):
        self.load_params = params
        if isinstance(self.markets, Exception):
            raise self.markets
        return self.markets

    def fetch_time(self):
        return self.now_ms

    def fetch_order_book(self, symbol, limit=None):
        self.book_calls += 1
        if self.book_calls <= self.fail_books:
            raise RuntimeError("upstream timeout at https://example.invalid/api")
        return {
            "bids": [[self.mid - 0.5, 1.0, 123], [self.mid - 1.0, 2.0, 124]],
            "asks": [[self.mid + 0.5, 1.0, 125], [self.mid + 1.0, 2.0, 126]],
            "timestamp": None,
        }

    def fetch_trades(self, symbol, limit=None):
        self.trade_id += 1
        stamp = self.now_ms + self.trade_id * 1000
        # The same trade comes back twice in a row: the recorder must dedupe it.
        return [
            {"id": str(self.trade_id), "timestamp": stamp, "price": self.mid, "amount": 0.01},
            {"id": str(self.trade_id), "timestamp": stamp, "price": self.mid, "amount": 0.01},
            {"id": "old", "timestamp": self.now_ms - 60_000, "price": self.mid, "amount": 1},
        ]


def default_markets() -> dict:
    def market(symbol, price_step=0.01, amount_step=0.00001, active=True):
        return {
            "symbol": symbol,
            "spot": True,
            "active": active,
            "precision": {"price": price_step, "amount": amount_step},
            "limits": {"amount": {"min": amount_step}, "cost": {"min": 5.0}},
        }

    return {
        "BTC/USDT": market("BTC/USDT"),
        "ETH/USDT": market("ETH/USDT"),
        "SOL/USDT": market("SOL/USDT", active=False),
        "PEPE/USDT": market("PEPE/USDT"),
        "BTC/USD": market("BTC/USD", price_step=0.1),
        "SOL/USD": market("SOL/USD"),
    }


def fake_clients(client: FakeClient | None = None) -> PublicClients:
    shared = client or FakeClient()
    return PublicClients(lambda exchange_id: shared)
