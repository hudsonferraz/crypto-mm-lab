from datetime import UTC, datetime

from app.models.domain import ArbitrageDirection, Opportunity
from app.storage.repository import Repository


def test_repository_persists_opportunities(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    repo = Repository(db_url)
    repo.initialize()
    now = datetime.now(UTC)

    opportunity = Opportunity(
        direction=ArbitrageDirection.BUY_AMM_SELL_CEX,
        cex_mid=3100.0,
        amm_price=3000.0,
        trial_trade_size=0.5,
        gross_edge=50.0,
        cex_fee=1.0,
        amm_fee=2.0,
        gas_cost=3.0,
        slippage_cost=4.0,
        net_edge=40.0,
        net_edge_bps=25.0,
        timestamp=now,
    )
    repo.save_opportunities([opportunity])
    loaded = repo.get_latest_opportunities(limit=1)

    assert len(loaded) == 1
    assert loaded[0].direction == ArbitrageDirection.BUY_AMM_SELL_CEX
    assert loaded[0].net_edge_bps == 25.0
    repo.close()
