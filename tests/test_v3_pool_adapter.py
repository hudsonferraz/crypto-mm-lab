import asyncio

import pytest

from app.adapters.dex import abis
from app.adapters.dex.multicall import Call, Multicall, selector
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter, V3PoolUnavailable
from app.market_data.uniswap_v3.swap_simulator import pool_state_from_snapshot, simulate_exact_input
from tests.fake_chain import POOL, FakeChain
from tests.v3_helpers import window_with_positions

L = 10**20
TICK = 199_125


def _chain(**kwargs) -> FakeChain:
    window = window_with_positions(
        [(198_000, 200_000, L), (199_000, 199_300, 3 * L)],
        spacing=10,
        min_word=75,
        max_word=80,
        outside={199_000: (5, 6), 199_300: (7, 8)},
    )
    return FakeChain(window, tick=TICK, liquidity=4 * L, **kwargs)


def _adapter(chain: FakeChain, **kwargs) -> V3PoolAdapter:
    return V3PoolAdapter(chain, POOL, retry_delay_sec=0, **kwargs)


def test_reads_block_pinned_state_window_and_bound_ticks() -> None:
    chain = _chain()
    adapter = _adapter(chain, window_words=2)
    snapshot = adapter.read_snapshot((199_000, 199_300))

    assert snapshot.block_number == 100
    assert snapshot.tick == TICK and snapshot.liquidity == 4 * L
    assert snapshot.metadata.base_is_token0 is False
    assert snapshot.metadata.fee_pips == 500 and snapshot.metadata.tick_spacing == 10
    assert 1_000 < snapshot.price < 5_000
    assert snapshot.base_fee_wei == 12 * 10**9
    assert snapshot.lower_tick_info.fee_growth_outside0_x128 == 5
    assert snapshot.upper_tick_info.initialized
    assert snapshot.window_is_current and not snapshot.is_incomplete and not snapshot.is_stale
    assert set(snapshot.window.ticks) == {198_000, 199_000, 199_300, 200_000}
    # every window read was pinned to the state block
    pinned = [
        block
        for sel, block in chain.calls
        if sel == selector("tryAggregate(bool,(address,bytes)[])").hex()
    ]
    assert pinned and all(block == 100 for block in pinned)


def test_window_refreshes_on_cadence_and_quotes_go_inexact_between() -> None:
    chain = _chain()
    adapter = _adapter(chain, window_words=2, window_refresh_ticks=3)
    first = adapter.read_snapshot()
    chain.block = 101
    second = adapter.read_snapshot()
    assert second.window is first.window and not second.window_is_current
    quote = simulate_exact_input(
        pool_state_from_snapshot(second), second.window, zero_for_one=True, amount_in=10**9
    )
    assert not quote.exact
    chain.block = 102
    adapter.read_snapshot()
    chain.block = 103
    fourth = adapter.read_snapshot()
    assert fourth.window_is_current


def test_window_refreshes_when_price_walks_off_loaded_words() -> None:
    chain = _chain()
    adapter = _adapter(chain, window_words=1, window_refresh_ticks=1000)
    first = adapter.read_snapshot()
    chain.tick = 199_125 + 2560 * 3
    chain.block = 101
    second = adapter.read_snapshot()
    assert second.window is not first.window
    assert second.window.block_number == 101


def test_failed_bound_tick_marks_snapshot_incomplete() -> None:
    chain = _chain()
    adapter = _adapter(chain)
    adapter.read_snapshot()  # metadata + window
    chain.fail_selectors.add(selector(abis.V3_TICKS[0]))
    snapshot = adapter.read_snapshot((199_000, 199_300))
    assert snapshot.is_incomplete
    assert snapshot.lower_tick_info is None


def test_partial_bitmap_failure_keeps_contiguous_words_and_flags_incomplete() -> None:
    chain = _chain()
    chain.fail_words = {79}
    adapter = _adapter(chain, window_words=2)
    snapshot = adapter.read_snapshot()
    assert snapshot.is_incomplete
    assert snapshot.window.max_word == 78


def test_window_failure_keeps_previous_window() -> None:
    chain = _chain()
    adapter = _adapter(chain, window_words=1, window_refresh_ticks=1)
    first = adapter.read_snapshot()
    chain.fail_words = {77, 78, 76}
    chain.block = 101
    second = adapter.read_snapshot()
    assert second.window is first.window
    assert second.is_incomplete


def test_core_failure_returns_stale_last_good_then_raises_without_history() -> None:
    chain = _chain()
    adapter = _adapter(chain, max_retries=2)
    good = adapter.read_snapshot()
    chain.fail_everything = True
    stale = adapter.read_snapshot()
    assert stale.is_stale and stale.block_number == good.block_number
    assert adapter.failures_total == 1

    fresh = _adapter(_chain(), max_retries=1)
    fresh._multicall = Multicall(_FailingTransport())
    with pytest.raises(V3PoolUnavailable):
        fresh.read_snapshot()


def test_core_subcall_failure_is_a_read_failure() -> None:
    chain = _chain()
    adapter = _adapter(chain, max_retries=1)
    adapter.read_snapshot()
    chain.fail_selectors.add(selector(abis.V3_SLOT0[0]))
    assert adapter.read_snapshot().is_stale


def test_async_fetch_snapshot() -> None:
    snapshot = asyncio.run(_adapter(_chain()).fetch_snapshot())
    assert snapshot.block_number == 100


def test_call_helpers_parse_tuple_signatures() -> None:
    call = Call(POOL, "quoteExactInputSingle((address,address,uint256,uint24,uint160))")
    assert call.input_types == ["(address,address,uint256,uint24,uint160)"]
    assert Call(POOL, "ticks(int24)").input_types == ["int24"]
    assert Call(POOL, "slot0()").input_types == []


class _FailingTransport:
    def eth_call(self, to, data, block):
        raise ConnectionError("down")
