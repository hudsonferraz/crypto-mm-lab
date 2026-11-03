"""Block replay (US5): every swap in a block reproduced with its kind inferred."""

from app.adapters.dex.pool_events import parse_logs
from app.market_data.uniswap_v2_math import get_amount_out
from app.services.mempool.pool_state import PoolStateTracker
from app.services.mempool.predictor import ScanParams
from app.services.mempool.replay import replay_block
from tests.mempool_helpers import (
    T0,
    TICK,
    V2_PAIR,
    V3_POOL,
    BlockBuilder,
    config,
    pair_state,
    tx_hash,
)

CFG = config()
PARAMS = ScanParams(1_000.0, 160_000, 0.05, 30.0)
THIN = 3 * 10**18


def test_replay_reproduces_every_kind_exactly_and_measures_ordering():
    start = pair_state(100, liquidity=THIN, positions=[(TICK - 6000, TICK + 6000, THIN)])
    builder = BlockBuilder(start, 101)
    t1, t2, t3, t4, t5 = (tx_hash(f"r{i}") for i in range(5))
    builder.v3(t1, "sell_weth", "exact_in", 3 * 10**18)
    builder.v3(t2, "buy_weth", "exact_out", 2 * 10**18)
    limit = builder.state.v3.sqrt_price_x96 * 10001 // 10000  # selling WETH raises sqrtPrice
    builder.v3(t3, "sell_weth", "exact_in", 10**24, limit=limit)
    builder.v2(t4, "sell_weth", "exact_in", 10**18)
    fair = get_amount_out(10**17, builder.state.v2.reserve_weth, builder.state.v2.reserve_usdc)
    builder.v2(t5, "sell_weth", "exact_amounts", 10**17, fixed_out=fair - 1_000)

    tracker = PoolStateTracker(CFG)
    tracker.anchor(start)
    block = tracker.apply_block(
        builder.header(), parse_logs(builder.logs, v2_pair=V2_PAIR, v3_pool=V3_POOL)
    )
    records = {r.tx_hash: r for r in replay_block(CFG, block, PARAMS, now=T0)}

    assert set(records) == {t1, t2, t3, t4, t5}
    kinds = {h: (r.legs[0].kind, r.legs[0].sqrt_price_limit_x96) for h, r in records.items()}
    assert kinds[t1] == ("exact_in", None)
    assert kinds[t2][0] == "exact_out"
    assert kinds[t3] == ("exact_in", limit)
    assert kinds[t4][0] == "exact_in"
    assert kinds[t5][0] == "exact_amounts"
    for record in records.values():
        assert record.mode == "replay" and record.route == "replay"
        assert record.status == "included"
        assert "kind_inferred" in record.legs[0].flags
        assert all(leg.exact and leg.math_error_bps == 0 for leg in record.outcome.legs)
        assert record.seen_head_block == 100 and record.first_seen_at == block.header.timestamp
    assert records[t1].outcome.legs[0].ordering_effect_bps == 0  # first in the block
    assert records[t2].outcome.legs[0].ordering_effect_bps > 0  # t1 moved V3 first
    assert records[t4].outcome.legs[0].ordering_effect_bps == 0  # first V2 trade


def test_replay_without_scanner_and_empty_block():
    start = pair_state(100)
    tracker = PoolStateTracker(CFG)
    tracker.anchor(start)
    builder = BlockBuilder(start, 101)
    builder.plain(tx_hash("nothing"))
    block = tracker.apply_block(builder.header(), [])
    assert replay_block(CFG, block, PARAMS, now=T0, run_scanner=False) == []
