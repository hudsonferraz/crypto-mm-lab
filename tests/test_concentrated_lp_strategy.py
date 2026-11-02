from datetime import UTC, datetime, timedelta

import pytest

from app.strategies.concentrated_lp import ConcentratedLpStrategy, in_range
from tests.v3_helpers import snapshot_at_tick, window_with_positions

L = 3 * 10**18
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _strategy(**kwargs) -> ConcentratedLpStrategy:
    params = dict(
        range_width_bps=200,
        rebalance_threshold_bps=80,
        cooldown_sec=300,
        prefer_initialized_ticks=False,
        snap_tolerance_spacings=3,
    )
    params.update(kwargs)
    return ConcentratedLpStrategy(**params)


def test_in_range_is_lower_inclusive_upper_exclusive() -> None:
    assert in_range(100, 100, 200)
    assert in_range(199, 100, 200)
    assert not in_range(200, 100, 200)
    assert not in_range(99, 100, 200)


def test_target_range_is_spacing_aligned_and_contains_center() -> None:
    snapshot = snapshot_at_tick(199_125, L)
    target = _strategy().target_range(snapshot.price, snapshot)
    assert target.tick_lower % 10 == 0 and target.tick_upper % 10 == 0
    assert in_range(snapshot.tick, target.tick_lower, target.tick_upper)
    assert 190 <= target.tick_upper - target.tick_lower <= 220
    assert not target.snapped_to_initialized


def test_target_range_snaps_to_nearby_initialized_ticks() -> None:
    plain = snapshot_at_tick(199_125, L)
    raw = _strategy().target_range(plain.price, plain)
    window = window_with_positions(
        [(raw.tick_lower - 20, raw.tick_upper + 10, L)], spacing=10, min_word=76, max_word=78
    )
    snapshot = snapshot_at_tick(199_125, L, window=window)
    snapped = _strategy(prefer_initialized_ticks=True).target_range(snapshot.price, snapshot)
    assert (snapped.tick_lower, snapped.tick_upper) == (raw.tick_lower - 20, raw.tick_upper + 10)
    assert snapped.snapped_to_initialized
    far = _strategy(prefer_initialized_ticks=True, snap_tolerance_spacings=1)
    assert far.target_range(snapshot.price, snapshot).tick_lower == raw.tick_lower


def test_tiny_width_still_produces_one_spacing_range() -> None:
    snapshot = snapshot_at_tick(199_125, L)
    target = _strategy(range_width_bps=0.1).target_range(snapshot.price, snapshot)
    assert target.tick_upper - target.tick_lower == 10


def test_validate_width_rejects_sub_spacing_ranges() -> None:
    with pytest.raises(ValueError, match="narrower than one tick spacing"):
        _strategy(range_width_bps=5).validate_width(10)
    _strategy(range_width_bps=10).validate_width(10)


def _reason(strategy, snapshot, **overrides):
    params = dict(
        has_position=True,
        tick_lower=199_000,
        tick_upper=199_200,
        range_center=snapshot.price,
        snapshot=snapshot,
        last_rebalance_at=None,
        now=T0,
        kill_switch_active=False,
    )
    params.update(overrides)
    return strategy.rebalance_reason(**params)


def test_rebalance_reasons() -> None:
    strategy = _strategy()
    inside = snapshot_at_tick(199_100, L)
    assert _reason(strategy, inside) is None
    assert _reason(strategy, inside, has_position=False) == "open"
    assert _reason(strategy, snapshot_at_tick(199_200, L), range_center=inside.price) == (
        "out_of_range"
    )
    assert _reason(strategy, inside, range_center=inside.price * 1.01) == "drift"
    # cooldown, kill switch, and stale data all block
    assert (
        _reason(
            strategy, snapshot_at_tick(199_300, L), last_rebalance_at=T0 - timedelta(seconds=10)
        )
        is None
    )
    assert _reason(strategy, inside, has_position=False, kill_switch_active=True) is None
    assert (
        _reason(strategy, snapshot_at_tick(199_100, L, is_stale=True), has_position=False) is None
    )


def test_cex_center_far_from_pool_price_falls_back_to_pool_price() -> None:
    snapshot = snapshot_at_tick(199_125, L)
    target = _strategy().target_range(snapshot.price * 0.97, snapshot)
    assert in_range(snapshot.tick, target.tick_lower, target.tick_upper)
    assert target.center_price == snapshot.price
    near = _strategy().target_range(snapshot.price * 1.002, snapshot)
    assert near.center_price == snapshot.price * 1.002
