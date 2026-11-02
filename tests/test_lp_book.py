import random
from datetime import UTC, datetime, timedelta

import pytest

from app.execution.lp_paper_book import LpPaperBook
from app.market_data.uniswap_v3.constants import Q128, U256
from app.market_data.uniswap_v3.fee_growth import fees_owed
from app.models.v3 import TickInfo
from app.strategies.concentrated_lp import ConcentratedLpStrategy
from tests.v3_helpers import snapshot_at_tick, window_with_positions

L_POOL = 3 * 10**18
T0 = datetime(2026, 1, 1, tzinfo=UTC)
CAPITAL = 10_000.0
WINDOW = window_with_positions([(180_000, 220_000, L_POOL)], spacing=10, min_word=68, max_word=86)


def _book(**kwargs) -> LpPaperBook:
    strategy_kwargs = dict(
        range_width_bps=200,
        rebalance_threshold_bps=80,
        cooldown_sec=kwargs.pop("cooldown_sec", 0),
        prefer_initialized_ticks=False,
    )
    return LpPaperBook(
        strategy=ConcentratedLpStrategy(**strategy_kwargs),
        capital_quote=kwargs.pop("capital", CAPITAL),
        gas_units=450_000,
        priority_fee_gwei=0.05,
        fallback_gas_price_gwei=30,
    )


def _snap(tick, i=0, *, fg0=0, fg1=0, bounds=None, outside=(0, 0, 0, 0), initialized=True, **kw):
    infos = {}
    if bounds is not None:
        lo, hi = bounds
        infos = dict(
            lower_info=TickInfo(lo, 1, 1, outside[0], outside[1], initialized),
            upper_info=TickInfo(hi, 1, -1, outside[2], outside[3], initialized),
        )
    return snapshot_at_tick(
        tick,
        L_POOL,
        window=WINDOW,
        block=100 + i,
        fg0=fg0,
        fg1=fg1,
        timestamp=T0 + timedelta(seconds=12 * i),
        **infos,
        **kw,
    )


def _diluted(fee: int, liquidity: int) -> int:
    """Fee after sharing growth with the paper position's own liquidity."""
    return fee * L_POOL // (L_POOL + liquidity)


def _assert_reconciles(state) -> None:
    assert abs(state.reconciliation_error_quote) < CAPITAL * 1e-4


def test_open_sizes_position_to_capital_and_pays_swap_and_gas() -> None:
    book = _book()
    state, events = book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    assert state.status == "in_range"
    assert state.liquidity > 0 and state.epoch == 1 and state.rebalance_count == 0
    assert events and events[0].reason == "open"
    assert events[0].swap_sells_base is False  # starts all USDC, buys WETH
    assert 0 < state.swap_cost_quote < CAPITAL * 0.002  # ~5 bps fee on ~half the capital
    assert state.gas_cost_quote > 0
    assert state.value_quote == pytest.approx(
        CAPITAL - state.swap_cost_quote - state.gas_cost_quote, rel=1e-6
    )
    assert state.idle_quote >= 0 and state.idle_base >= 0
    assert state.price_lower < state.pool_price < state.price_upper
    _assert_reconciles(state)


def test_exact_fee_accrual_uses_fee_growth_inside() -> None:
    book = _book(cooldown_sec=10**9)
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    bounds = book.bounds
    liquidity = book.last_state.liquidity
    book.step(_snap(199_125, 1, bounds=bounds), center_price=None, kill_switch_active=False)
    growth0, growth1 = 5 * Q128 // 10**12, 7 * Q128 // 10**20
    state, _ = book.step(
        _snap(199_125, 2, fg0=growth0, fg1=growth1, bounds=bounds),
        center_price=None,
        kill_switch_active=False,
    )
    assert state.fee_accounting == "exact"
    assert state.fees_owed_quote == _diluted(
        fees_owed(liquidity, growth0), liquidity
    )  # token0 = USDC = quote
    assert state.fees_owed_base == _diluted(fees_owed(liquidity, growth1), liquidity)
    assert state.fees_quote > 0
    _assert_reconciles(state)


def test_no_fees_out_of_range_and_approximate_without_initialized_bounds() -> None:
    book = _book(cooldown_sec=10**9)
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    out = book.step(
        _snap(199_600, 1, fg0=10**40, fg1=10**40), center_price=None, kill_switch_active=False
    )[0]
    out2 = book.step(
        _snap(199_600, 2, fg0=2 * 10**40, fg1=2 * 10**40),
        center_price=None,
        kill_switch_active=False,
    )[0]
    assert out2.status == "out_of_range"
    assert out2.fee_accounting == "approximate"
    assert out2.fees_quote == out.fees_quote  # nothing accrued while out of range both ends
    _assert_reconciles(out2)


def test_uninitialized_bounds_fall_back_to_approximate_in_range() -> None:
    book = _book(cooldown_sec=10**9)
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    bounds = book.bounds
    state = book.step(
        _snap(199_125, 1, fg0=10**30, bounds=bounds, initialized=False),
        center_price=None,
        kill_switch_active=False,
    )[0]
    assert state.fee_accounting == "approximate"
    assert state.fees_owed_quote == _diluted(
        fees_owed(book.last_state.liquidity, 10**30), book.last_state.liquidity
    )


def test_reinitialized_bound_tick_guard_uses_approximate() -> None:
    book = _book(cooldown_sec=10**9)
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    bounds = book.bounds
    book.step(
        _snap(199_125, 1, fg0=100, fg1=100, bounds=bounds, outside=(90, 90, 5, 5)),
        center_price=None,
        kill_switch_active=False,
    )
    # lower tick re-initialized with outside reset to 0 -> inside jumps beyond global growth
    state = book.step(
        _snap(199_125, 2, fg0=110, fg1=110, bounds=bounds, outside=(0, 0, 5, 5)),
        center_price=None,
        kill_switch_active=False,
    )[0]
    assert state.fee_accounting == "approximate"


def test_fee_growth_wraparound_is_handled_exactly() -> None:
    book = _book(cooldown_sec=10**9)
    book.step(_snap(199_125, fg0=U256 - Q128), center_price=None, kill_switch_active=False)
    bounds = book.bounds
    liquidity = book.last_state.liquidity
    book.step(
        _snap(199_125, 1, fg0=U256 - Q128, bounds=bounds),
        center_price=None,
        kill_switch_active=False,
    )
    state = book.step(
        _snap(199_125, 2, fg0=Q128, bounds=bounds), center_price=None, kill_switch_active=False
    )[0]
    assert state.fee_accounting == "exact"
    assert state.fees_owed_quote == _diluted(fees_owed(liquidity, 2 * Q128), liquidity)


def test_out_of_range_rebalance_records_costs_and_realized_il() -> None:
    book = _book(cooldown_sec=60)
    first = book.step(_snap(199_125), center_price=None, kill_switch_active=False)[0]
    moved = _snap(199_600, 10)
    state, events = book.step(moved, center_price=moved.price, kill_switch_active=False)
    assert state.rebalance_count == 1 and state.epoch == 2
    assert events[0].reason == "out_of_range"
    assert events[0].realized_il_quote < 0  # price moved away: IL is a loss
    # gas is paid in ETH, so the second payment is valued at the new (lower) price
    second_gas = first.gas_cost_quote * moved.price / first.pool_price
    assert state.gas_cost_quote == pytest.approx(first.gas_cost_quote + second_gas, rel=1e-9)
    assert state.status == "in_range"
    _assert_reconciles(state)


def test_drift_rebalance_and_cooldown() -> None:
    book = _book(cooldown_sec=60)
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    # 90 ticks ~ 90 bps drift, still inside the 200 bps range -> drift trigger
    early = book.step(_snap(199_035, 1), center_price=None, kill_switch_active=False)[0]
    assert early.rebalance_count == 0  # cooldown (12 s < 60 s)
    late, events = book.step(_snap(199_035, 10), center_price=None, kill_switch_active=False)
    assert events and events[0].reason == "drift" and late.rebalance_count == 1


def test_kill_switch_freezes_book() -> None:
    book = _book()
    idle = book.step(_snap(199_125), center_price=None, kill_switch_active=True)[0]
    assert idle.status == "not_opened" and idle.liquidity == 0
    book.step(_snap(199_125, 1), center_price=None, kill_switch_active=False)
    frozen, events = book.step(_snap(199_900, 2), center_price=None, kill_switch_active=True)
    assert frozen.status == "frozen" and not events and frozen.rebalance_count == 0
    _assert_reconciles(frozen)


def test_stale_snapshot_skips_accrual_and_rebalance() -> None:
    book = _book()
    opened = book.step(_snap(199_125), center_price=None, kill_switch_active=False)[0]
    stale, events = book.step(
        _snap(199_900, 1, fg0=10**40, is_stale=True), center_price=None, kill_switch_active=False
    )
    assert not events and stale.fees_quote == opened.fees_quote
    assert "stale" in stale.status_note


def test_waiting_for_window_and_capital_too_small() -> None:
    book = _book()
    no_window = snapshot_at_tick(199_125, L_POOL, window=None)
    state = book.step(no_window, center_price=None, kill_switch_active=False)[0]
    assert state.status == "not_opened" and "tick window" in state.status_note

    tiny = _book(capital=1e-9)
    state = tiny.step(_snap(199_125), center_price=None, kill_switch_active=False)[0]
    assert state.status == "not_opened" and "below minimum" in state.status_note


def test_rebalance_skipped_when_swap_exceeds_window() -> None:
    narrow = window_with_positions(
        [(199_000, 199_300, 10**9)], spacing=10, min_word=77, max_word=77
    )
    book = _book()
    snapshot = snapshot_at_tick(199_125, 10**9, window=narrow)
    state = book.step(snapshot, center_price=None, kill_switch_active=False)[0]
    assert state.status == "not_opened" and "window_exhausted" in state.status_note


def test_rejects_width_narrower_than_spacing_on_first_snapshot() -> None:
    book = LpPaperBook(
        strategy=ConcentratedLpStrategy(
            range_width_bps=5, rebalance_threshold_bps=1, cooldown_sec=0
        ),
        capital_quote=CAPITAL,
        gas_units=1,
        priority_fee_gwei=0,
        fallback_gas_price_gwei=1,
    )
    with pytest.raises(ValueError):
        book.step(_snap(199_125), center_price=None, kill_switch_active=False)


def test_checkpoint_restore_round_trip() -> None:
    book = _book()
    book.step(_snap(199_125), center_price=None, kill_switch_active=False)
    checkpoint = book.checkpoint()
    before = book.last_state
    book.step(_snap(199_900, 10), center_price=None, kill_switch_active=False)
    assert book.last_state.rebalance_count == 1
    book.restore(checkpoint)
    assert book.last_state == before and book.bounds is not None


def test_fallback_gas_price_when_basefee_missing() -> None:
    book = _book()
    state, _ = book.step(
        _snap(199_125, base_fee_wei=None), center_price=None, kill_switch_active=False
    )
    expected = 450_000 * 30e9 / 1e18 * state.pool_price
    assert state.gas_cost_quote == pytest.approx(expected)


def test_reconciliation_holds_along_a_long_random_path() -> None:
    rng = random.Random(7)
    book = _book(cooldown_sec=120)
    tick, fg0, fg1 = 199_125, 0, 0
    worst = 0.0
    for i in range(300):
        tick += rng.randint(-25, 25)
        fg0 += rng.randint(0, 10**26)
        fg1 += rng.randint(0, 10**14) * 10**12
        bounds = book.bounds
        snapshot = _snap(tick, i, fg0=fg0, fg1=fg1, bounds=bounds)
        state, _ = book.step(snapshot, center_price=None, kill_switch_active=False)
        worst = max(worst, abs(state.reconciliation_error_quote))
        # components add up to the independently computed value
        _assert_reconciles(state)
    assert state.rebalance_count > 0
    assert state.fees_quote > 0
    assert worst < CAPITAL * 1e-4
    assert 0 < state.time_in_range_sec <= state.time_tracked_sec


def test_fee_dilution_grows_with_position_share() -> None:
    small, big = _book(cooldown_sec=10**9), _book(cooldown_sec=10**9, capital=1_000_000)
    states = []
    for book in (small, big):
        book.step(_snap(199_125), center_price=None, kill_switch_active=False)
        bounds = book.bounds
        book.step(_snap(199_125, 1, bounds=bounds), center_price=None, kill_switch_active=False)
        state, _ = book.step(
            _snap(199_125, 2, fg0=10**30, bounds=bounds),
            center_price=None,
            kill_switch_active=False,
        )
        states.append(state)
    small_state, big_state = states
    assert 0 < small_state.pool_share_pct < big_state.pool_share_pct
    undiluted = fees_owed(big_state.liquidity, 10**30)
    assert big_state.fees_owed_quote == _diluted(undiluted, big_state.liquidity) < undiluted
