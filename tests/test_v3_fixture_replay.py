import asyncio

from app.adapters.dex.v3_fixture_replay import V3FixtureReplay
from app.adapters.dex.v3_fixtures import load_quote_vectors, observation_row, write_observations
from app.market_data.uniswap_v3.constants import U256

VECTORS = load_quote_vectors()


def _rows(count: int) -> list[dict]:
    from dataclasses import replace

    base = VECTORS.snapshot
    return [
        observation_row(
            replace(
                base,
                block_number=base.block_number + i,
                fee_growth_global0_x128=base.fee_growth_global0_x128 + i * 1000,
                fee_growth_global1_x128=base.fee_growth_global1_x128 + i * 2000,
            )
        )
        for i in range(count)
    ]


def test_replay_cycles_with_monotonic_fee_growth_and_blocks(tmp_path) -> None:
    path = tmp_path / "obs.csv"
    write_observations(path, _rows(3))
    replay = V3FixtureReplay(observations_path=path)
    snapshots = [replay.read_snapshot() for _ in range(7)]
    assert all(s.data_source == "fixture" and s.window is not None for s in snapshots)
    blocks = [s.block_number for s in snapshots]
    assert blocks == sorted(blocks) and len(set(blocks)) == 7
    growth = [s.fee_growth_global0_x128 for s in snapshots]
    deltas = [(b - a) % U256 for a, b in zip(growth, growth[1:], strict=False)]
    assert all(delta < 10**6 for delta in deltas)
    times = [s.block_timestamp for s in snapshots]
    assert times == sorted(times)
    assert replay.metadata.fee_pips == 500
    assert asyncio.run(replay.fetch_snapshot()).data_source == "fixture"


def test_replay_rejects_empty_fixture(tmp_path) -> None:
    import pytest

    path = tmp_path / "stale.csv"
    rows = _rows(1)
    rows[0]["is_stale"] = "true"
    write_observations(path, rows)
    with pytest.raises(ValueError):
        V3FixtureReplay(observations_path=path)
