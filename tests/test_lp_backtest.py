"""SC-006: deterministic, offline LP backtests and sweeps over recorded mainnet data."""

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.dex.v3_fixtures import (
    DEFAULT_OBSERVATIONS_PATH,
    load_observations,
    load_quote_vectors,
    observation_row,
    write_observations,
)
from app.config.settings import Settings
from app.market_data.uniswap_v3.tick_math import get_sqrt_ratio_at_tick
from app.services.lp_backtest_runner import (
    run_lp_backtest,
    run_lp_sweep_from_fixture,
    run_lp_sweep_from_repository,
)

VECTORS = load_quote_vectors()


def _settings(tmp_path, **overrides) -> Settings:
    params = dict(
        db_url=f"sqlite:///{tmp_path / 'bt.db'}",
        lp_rebalance_cooldown_sec=0,
        lp_prefer_initialized_ticks=False,
    )
    params.update(overrides)
    return Settings(_env_file=None, **params)


def _synthetic_rows(count: int = 120) -> list[dict]:
    """A price path that oscillates +/-60 ticks around the recorded tick."""
    base = VECTORS.snapshot
    rows = []
    for i in range(count):
        tick = base.tick + int(60 * ((i % 40) - 20) / 20)
        snapshot = base.__class__(
            **{
                **{f: getattr(base, f) for f in base.__dataclass_fields__},
                "sqrt_price_x96": get_sqrt_ratio_at_tick(tick) + 1,
                "tick": tick,
                "fee_growth_global0_x128": base.fee_growth_global0_x128 + i * 10**28,
                "fee_growth_global1_x128": base.fee_growth_global1_x128 + i * 10**40,
                "block_number": base.block_number + i,
                "block_timestamp": base.block_timestamp + timedelta(seconds=12 * i),
                "window": None,
            }
        )
        rows.append(observation_row(snapshot))
    return rows


def test_backtest_is_deterministic_and_reconciles(tmp_path) -> None:
    rows = _synthetic_rows()
    kwargs = dict(
        metadata=VECTORS.snapshot.metadata,
        window=VECTORS.snapshot.window,
        width_bps=50,
        threshold_bps=20,
    )
    first = run_lp_backtest(_settings(tmp_path), rows, **kwargs)
    second = run_lp_backtest(_settings(tmp_path), rows, **kwargs)
    assert first == second
    assert first.observation_count == 120
    assert first.rebalance_count > 0
    assert first.fees_quote > 0
    assert 0 < first.time_in_range_pct <= 100
    assert first.fee_accounting_exact_pct == 0  # recorded data has no bound-tick counters
    assert first.max_reconciliation_error_quote < first.capital_quote * 1e-4
    components = (
        first.fees_quote
        + first.il_quote
        + first.hodl_pnl_quote
        - first.swap_cost_quote
        - first.gas_cost_quote
    )
    assert components == pytest.approx(first.net_pnl_quote, abs=1.0)


def test_sweep_over_fixture_file_and_wider_ranges_rebalance_less(tmp_path) -> None:
    path = tmp_path / "obs.csv"
    write_observations(path, _synthetic_rows())
    results = run_lp_sweep_from_fixture(
        _settings(tmp_path), path, widths_bps=[40, 400], thresholds_bps=[15, 150]
    )
    assert len(results) == 4
    by_key = {(r.range_width_bps, r.rebalance_threshold_bps): r for r in results}
    assert by_key[(400, 150)].rebalance_count <= by_key[(40, 15)].rebalance_count


def test_empty_backtest_and_invalid_width(tmp_path) -> None:
    empty = run_lp_backtest(
        _settings(tmp_path),
        [],
        metadata=VECTORS.snapshot.metadata,
        window=VECTORS.snapshot.window,
        width_bps=100,
        threshold_bps=50,
    )
    assert empty.observation_count == 0 and empty.final_status == "not_opened"
    with pytest.raises(ValueError):
        run_lp_backtest(
            _settings(tmp_path),
            _synthetic_rows(3),
            metadata=VECTORS.snapshot.metadata,
            window=VECTORS.snapshot.window,
            width_bps=2,
            threshold_bps=1,
        )


def test_backtest_from_repository_rows(tmp_path) -> None:
    from app.execution.lp_paper_book import LpPaperBook  # noqa: F401  (book used via runner)
    from app.models.domain import OrderBookLevel, OrderBookSnapshot, PnLSnapshot, Position
    from app.storage.repository import Repository

    settings = _settings(tmp_path)
    repo = Repository(settings.db_url)
    repo.initialize()
    now = datetime.now(UTC)
    from app.adapters.dex.v3_fixtures import snapshot_from_observation

    for i, row in enumerate(_synthetic_rows(10)):
        snapshot = snapshot_from_observation(row, VECTORS.snapshot.metadata)
        snapshot = snapshot.__class__(
            **{
                **{f: getattr(snapshot, f) for f in snapshot.__dataclass_fields__},
                "data_source": "live",
            }
        )
        repo.persist_tick(
            tick_id=f"t{i}",
            snapshot=OrderBookSnapshot(
                "BTC/USDT", (OrderBookLevel(1, 1),), (OrderBookLevel(2, 1),), now
            ),
            fills=[],
            quotes=[],
            position=Position("BTC/USDT", 0, 1, 0, now),
            pnl=PnLSnapshot("BTC/USDT", 0, 0, 0, 0, now),
            v3_snapshot=snapshot,
        )
    repo.close()
    [result] = run_lp_sweep_from_repository(settings, widths_bps=[100], thresholds_bps=[50])
    assert result.observation_count == 10


@pytest.mark.skipif(not DEFAULT_OBSERVATIONS_PATH.exists(), reason="fixture not recorded")
def test_bundled_mainnet_fixture_backtests_offline(tmp_path) -> None:
    rows = load_observations()
    assert len(rows) >= 100
    results = run_lp_sweep_from_fixture(
        _settings(tmp_path), DEFAULT_OBSERVATIONS_PATH, widths_bps=[20, 200], thresholds_bps=[10]
    )
    assert all(result.observation_count == len(rows) for result in results)
    assert results == run_lp_sweep_from_fixture(
        _settings(tmp_path), DEFAULT_OBSERVATIONS_PATH, widths_bps=[20, 200], thresholds_bps=[10]
    )
    assert all(result.fees_quote > 0 for result in results)
