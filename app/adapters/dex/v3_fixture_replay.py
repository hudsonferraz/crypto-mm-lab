"""Replay recorded mainnet V3 observations when live RPC is unavailable.

Used by keyless hosted demos whose outbound RPC is blocked. Observations cycle
from ``v3_pool_observations.csv`` and swaps use the static tick window recorded
in ``v3_quote_vectors.json``; every snapshot is labeled ``data_source="fixture"``.
Fee-growth counters are re-based on each cycle so they stay monotonic.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.adapters.dex.v3_fixtures import (
    DEFAULT_OBSERVATIONS_PATH,
    DEFAULT_QUOTE_VECTORS_PATH,
    load_observations,
    load_quote_vectors,
    snapshot_from_observation,
)
from app.market_data.uniswap_v3.constants import U256
from app.models.v3 import V3PoolSnapshot


class V3FixtureReplay:
    def __init__(
        self,
        observations_path: Path = DEFAULT_OBSERVATIONS_PATH,
        vectors_path: Path = DEFAULT_QUOTE_VECTORS_PATH,
    ) -> None:
        vectors = load_quote_vectors(vectors_path)
        self._metadata = vectors.snapshot.metadata
        self._window = vectors.snapshot.window
        self._rows = [
            row for row in load_observations(observations_path) if row["is_stale"] != "true"
        ]
        if not self._rows:
            raise ValueError("observation fixture has no usable rows")
        self._index = 0
        self._cycle = 0
        self._started = datetime.now(UTC)
        first, last = self._rows[0], self._rows[-1]
        self._growth_span = (
            (int(last["fee_growth_global0_x128"]) - int(first["fee_growth_global0_x128"])) % U256,
            (int(last["fee_growth_global1_x128"]) - int(first["fee_growth_global1_x128"])) % U256,
        )
        self._block_span = int(last["block_number"]) - int(first["block_number"]) + 1
        self.failures_total = 0

    @property
    def metadata(self):
        return self._metadata

    async def fetch_snapshot(self, bounds: tuple[int, int] | None = None) -> V3PoolSnapshot:
        return await asyncio.to_thread(self.read_snapshot, bounds)

    def read_snapshot(self, bounds: tuple[int, int] | None = None) -> V3PoolSnapshot:
        row = self._rows[self._index]
        snapshot = snapshot_from_observation(row, self._metadata, window=self._window)
        offset0 = self._growth_span[0] * self._cycle
        offset1 = self._growth_span[1] * self._cycle
        snapshot = replace(
            snapshot,
            fee_growth_global0_x128=(snapshot.fee_growth_global0_x128 + offset0) % U256,
            fee_growth_global1_x128=(snapshot.fee_growth_global1_x128 + offset1) % U256,
            block_number=snapshot.block_number + self._block_span * self._cycle,
            block_timestamp=self._started
            + timedelta(seconds=12 * (self._index + self._cycle * len(self._rows))),
        )
        self._index += 1
        if self._index >= len(self._rows):
            self._index = 0
            self._cycle += 1
        return snapshot
