import pytest

from app.simlab.engine import START_VALUE, SimSettings, SimulationFailed, simulate
from app.simlab.explain import summarize
from app.simlab.recording import load_recording, save_recording
from tests.simlab_helpers import make_recording

FLAT = [80_000.0] * 12
# Up then back down: a market maker with both sides resting gets filled both ways.
WAVE = [80_000.0, 80_010.0, 80_020.0, 80_010.0, 80_000.0, 79_990.0, 79_980.0, 79_990.0] * 3


def test_start_is_half_base_half_quote_and_flat_market_matches_holding() -> None:
    result = simulate(make_recording(FLAT), SimSettings("pure_mm", 10.0, "full_cross_fill"))
    assert result.start_value == START_VALUE
    assert result.start_base == pytest.approx(5_000 / 80_000)
    assert result.fill_count == 0
    assert result.final_value == pytest.approx(result.hold_value)
    assert result.edge_vs_hold == pytest.approx(0.0)


def test_holding_follows_the_price() -> None:
    mids = [80_000.0] * 11 + [88_000.0]
    result = simulate(make_recording(mids), SimSettings("pure_mm", 10.0, "full_cross_fill"))
    assert result.hold_value == pytest.approx(5_000 * 1.1 + 5_000)


def test_snapshot_fill_models_fill_on_a_moving_book() -> None:
    for model in ("full_cross_fill", "partial_fill", "latency_prob_fill", "calibrated_fill"):
        result = simulate(make_recording(WAVE), SimSettings("pure_mm", 2.0, model))
        assert result.fill_count > 0, model
        assert result.buy_count + result.sell_count == result.fill_count
        assert result.fees > 0


def test_same_inputs_give_the_same_result() -> None:
    for model in ("latency_prob_fill", "calibrated_fill", "trade_fill"):
        settings = SimSettings("inventory_skew", 2.0, model)
        trades = [(t + 0.5, 80_000.0 + (5 if t % 2 else -5), 0.01) for t in range(20)]
        first = simulate(make_recording(WAVE, trades), settings).to_dict()
        second = simulate(make_recording(WAVE, trades), settings).to_dict()
        assert first == second, model


def test_trade_fill_uses_trades_not_snapshot_crosses() -> None:
    # Book swings through the quotes, but no trade printed: trade_fill must not fill.
    no_trades = simulate(make_recording(WAVE), SimSettings("pure_mm", 2.0, "trade_fill"))
    assert no_trades.fill_count == 0
    assert any("No public trades" in note for note in no_trades.notes)
    # A trade below the resting bid in the next second fills it.
    trades = [(1.5, 79_000.0, 0.5)]
    result = simulate(make_recording(FLAT, trades), SimSettings("pure_mm", 10.0, "trade_fill"))
    assert result.buy_count == 1 and result.sell_count == 0
    assert result.fills[0]["size"] == pytest.approx(result.quote_size)
    assert result.notes == []


def test_quotes_follow_the_market_rules() -> None:
    result = simulate(make_recording(WAVE), SimSettings("pure_mm", 3.0, "full_cross_fill"))
    assert result.quote_size == 0.00125
    for fill in result.fills:
        assert round(fill["price"] * 100) == pytest.approx(fill["price"] * 100)


def test_too_few_snapshots_fail_with_a_reason() -> None:
    with pytest.raises(SimulationFailed, match="at least 10"):
        simulate(make_recording(FLAT[:5]), SimSettings("pure_mm", 10.0, "full_cross_fill"))


def test_series_is_downsampled_and_ends_at_the_end() -> None:
    mids = [80_000.0 + (i % 7) for i in range(700)]
    result = simulate(make_recording(mids), SimSettings("pure_mm", 10.0, "full_cross_fill"))
    assert len(result.series) == 300
    assert result.series[0]["t"] == 0
    assert result.series[-1]["t"] == 699


def test_every_strategy_runs() -> None:
    for strategy in ("pure_mm", "inventory_skew", "volatility_spread"):
        result = simulate(make_recording(WAVE), SimSettings(strategy, 2.0, "full_cross_fill"))
        assert result.snapshot_count == len(WAVE)


def test_summary_says_what_happened() -> None:
    settings = SimSettings("pure_mm", 2.0, "full_cross_fill")
    result = simulate(make_recording(WAVE), settings)
    lines = summarize(result, settings, "USDT")
    assert any("fills" in line for line in lines)
    assert any("holding" in line for line in lines)
    quiet = simulate(make_recording(FLAT), SimSettings("pure_mm", 50.0, "full_cross_fill"))
    assert any("No quote filled" in line for line in summarize(quiet, settings, "USDT"))


def test_recording_round_trips_through_a_file(tmp_path) -> None:
    recording = make_recording(WAVE, [(1.5, 80_000.0, 0.25)])
    path = tmp_path / "r.json.gz"
    save_recording(recording, path)
    loaded = load_recording(path)
    assert loaded.symbol == recording.symbol and loaded.rules == recording.rules
    assert loaded.snapshots == recording.snapshots
    assert loaded.trades == recording.trades
    settings = SimSettings("pure_mm", 2.0, "trade_fill")
    assert simulate(loaded, settings).to_dict() == simulate(recording, settings).to_dict()
