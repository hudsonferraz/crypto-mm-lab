from datetime import UTC, datetime

from app.config.settings import Settings
from app.models.domain import AmmPoolSnapshot
from app.services.market_maker_loop import MarketMakerLoop


def _fresh_pool_snapshot(spot_price: float) -> AmmPoolSnapshot:
    base_reserve = 1_000.0
    return AmmPoolSnapshot(
        pool_address="0xpool",
        base_reserve=base_reserve,
        quote_reserve=base_reserve * spot_price,
        spot_price=spot_price,
        timestamp=datetime.now(UTC),
        is_stale=False,
    )


def test_scan_opportunities_suppressed_when_primary_cex_is_stale() -> None:
    loop = MarketMakerLoop(Settings(dex_enabled=True))
    pool_snapshot = _fresh_pool_snapshot(3_000.0)

    opportunities = loop._scan_opportunities(
        cex_mid=3_100.0,
        primary_is_stale=True,
        compare_is_stale=False,
        pool_snapshot=pool_snapshot,
        eth_price_usd=3_100.0,
    )

    assert opportunities == []


def test_scan_opportunities_allowed_when_both_cex_sources_are_fresh() -> None:
    loop = MarketMakerLoop(
        Settings(dex_enabled=True, arbitrage_min_edge_bps=-1_000.0, gas_price_gwei=1.0)
    )
    pool_snapshot = _fresh_pool_snapshot(3_000.0)

    opportunities = loop._scan_opportunities(
        cex_mid=3_100.0,
        primary_is_stale=False,
        compare_is_stale=False,
        pool_snapshot=pool_snapshot,
        eth_price_usd=3_100.0,
    )

    assert opportunities
