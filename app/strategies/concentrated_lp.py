"""Range selection and rebalance rules for the paper concentrated-liquidity book."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.market_data.uniswap_v3.pricing import (
    ceil_to_spacing,
    floor_to_spacing,
    price_to_tick,
    usable_tick_bounds,
)
from app.models.v3 import RebalanceReason, V3PoolSnapshot


@dataclass(frozen=True, slots=True)
class RangeTarget:
    tick_lower: int
    tick_upper: int
    center_price: float
    snapped_to_initialized: bool


def in_range(tick: int, tick_lower: int, tick_upper: int) -> bool:
    """Protocol rule: a position is active when lower <= current tick < upper."""
    return tick_lower <= tick < tick_upper


class ConcentratedLpStrategy:
    def __init__(
        self,
        *,
        range_width_bps: float,
        rebalance_threshold_bps: float,
        cooldown_sec: float,
        prefer_initialized_ticks: bool = True,
        snap_tolerance_spacings: int = 3,
    ) -> None:
        self.range_width_bps = range_width_bps
        self.rebalance_threshold_bps = rebalance_threshold_bps
        self.cooldown_sec = cooldown_sec
        self.prefer_initialized_ticks = prefer_initialized_ticks
        self.snap_tolerance_spacings = snap_tolerance_spacings

    def validate_width(self, tick_spacing: int) -> None:
        # One tick is ~1 bp of price, so the range must span at least one spacing.
        if self.range_width_bps < tick_spacing:
            raise ValueError(
                f"LP_RANGE_WIDTH_BPS={self.range_width_bps:g} is narrower than one tick "
                f"spacing ({tick_spacing} ticks ~ {tick_spacing} bps) for this pool"
            )

    def target_range(self, center_price: float, snapshot: V3PoolSnapshot) -> RangeTarget:
        """Range of ``range_width_bps`` around ``center_price`` that contains the pool tick.

        The CEX mid is the preferred center, but a range that does not contain the
        current pool price earns nothing and would be rebalanced immediately, so in
        that case the range is centered on the pool price instead.
        """
        target = self._range_around(center_price, snapshot)
        if not in_range(snapshot.tick, target.tick_lower, target.tick_upper):
            target = self._range_around(snapshot.price, snapshot)
        return target

    def _range_around(self, center_price: float, snapshot: V3PoolSnapshot) -> RangeTarget:
        meta = snapshot.metadata
        spacing = meta.tick_spacing
        half = self.range_width_bps / 20_000
        kwargs = dict(
            base_is_token0=meta.base_is_token0,
            token0_decimals=meta.token0_decimals,
            token1_decimals=meta.token1_decimals,
        )
        tick_a = price_to_tick(center_price * (1 - half), **kwargs)
        tick_b = price_to_tick(center_price * (1 + half), **kwargs)
        lower = floor_to_spacing(min(tick_a, tick_b), spacing)
        upper = ceil_to_spacing(max(tick_a, tick_b), spacing)
        if upper <= lower:
            upper = lower + spacing
        min_usable, max_usable = usable_tick_bounds(spacing)
        lower, upper = max(lower, min_usable), min(upper, max_usable)

        snapped = False
        if self.prefer_initialized_ticks and snapshot.window is not None:
            new_lower = self._nearest_initialized(snapshot, lower, upper_bound=upper)
            new_upper = self._nearest_initialized(snapshot, upper, lower_bound=new_lower)
            if (
                new_lower < new_upper
                and (new_lower, new_upper) != (lower, upper)
                and in_range(snapshot.tick, new_lower, new_upper)
            ):
                snapped = new_lower != lower or new_upper != upper
                lower, upper = new_lower, new_upper
        return RangeTarget(lower, upper, center_price, snapped)

    def _nearest_initialized(
        self,
        snapshot: V3PoolSnapshot,
        tick: int,
        *,
        lower_bound: int | None = None,
        upper_bound: int | None = None,
    ) -> int:
        assert snapshot.window is not None
        tolerance = self.snap_tolerance_spacings * snapshot.metadata.tick_spacing
        candidates = [
            t
            for t, info in snapshot.window.ticks.items()
            if info.initialized
            and abs(t - tick) <= tolerance
            and (lower_bound is None or t > lower_bound)
            and (upper_bound is None or t < upper_bound)
        ]
        if not candidates:
            return tick
        return min(candidates, key=lambda t: (abs(t - tick), t))

    def rebalance_reason(
        self,
        *,
        has_position: bool,
        tick_lower: int | None,
        tick_upper: int | None,
        range_center: float | None,
        snapshot: V3PoolSnapshot,
        last_rebalance_at: datetime | None,
        now: datetime,
        kill_switch_active: bool,
    ) -> RebalanceReason | None:
        if kill_switch_active or snapshot.is_stale:
            return None
        if not has_position:
            return "open"
        if (
            last_rebalance_at is not None
            and (now - last_rebalance_at).total_seconds() < self.cooldown_sec
        ):
            return None
        assert tick_lower is not None and tick_upper is not None
        if not in_range(snapshot.tick, tick_lower, tick_upper):
            return "out_of_range"
        if range_center and range_center > 0:
            drift_bps = abs(snapshot.price - range_center) / range_center * 10_000
            if drift_bps > self.rebalance_threshold_bps:
                return "drift"
        return None
