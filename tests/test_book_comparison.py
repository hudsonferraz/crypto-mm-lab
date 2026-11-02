from dataclasses import replace
from datetime import UTC, datetime

from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.analytics.lp_report import comparison_to_dict
from app.execution.lp_paper_book import LpPaperBook
from app.models.domain import PnLSnapshot
from app.services.book_comparison import compare_books
from app.strategies.concentrated_lp import ConcentratedLpStrategy

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _lp_state():
    book = LpPaperBook(
        strategy=ConcentratedLpStrategy(
            range_width_bps=200, rebalance_threshold_bps=80, cooldown_sec=0
        ),
        capital_quote=10_000,
        gas_units=450_000,
        priority_fee_gwei=0.05,
        fallback_gas_price_gwei=30,
    )
    return book.step(load_quote_vectors().snapshot, center_price=None, kill_switch_active=False)[0]


def test_compare_since_inception_and_labels_mismatch() -> None:
    lp = _lp_state()
    cex_end = PnLSnapshot("BTC/USDT", 10.0, -4.0, 1.0, 5.0, NOW)
    comparison = compare_books(
        cex_start=None,
        cex_end=cex_end,
        cex_capital=10_000,
        cex_symbol="BTC/USDT",
        lp_start=None,
        lp_end=lp,
        lp_pool_label="WETH/USDC",
    )
    assert comparison.cex.net_pnl_quote == 5.0 and comparison.cex.return_pct == 0.05
    assert comparison.lp.net_pnl_quote == lp.net_pnl_quote < 0  # opening swap + gas
    assert comparison.lp.costs_quote == lp.swap_cost_quote + lp.gas_cost_quote
    assert comparison.winner == "cex"
    notes = " ".join(comparison.mismatch_notes)
    assert "Asset mismatch" in notes and "USDT" in notes
    payload = comparison_to_dict(comparison)
    assert payload["winner"] == "cex" and payload["window_start"] is None


def test_compare_over_window_uses_deltas() -> None:
    lp_end = _lp_state()
    lp_start = replace(lp_end, net_pnl_quote=lp_end.net_pnl_quote - 7.0, fees_quote=0.0)
    comparison = compare_books(
        cex_start=PnLSnapshot("ETH/USDT", 1.0, 0.0, 0.0, 1.0, NOW),
        cex_end=PnLSnapshot("ETH/USDT", 2.0, 0.0, 0.0, 2.0, NOW),
        cex_capital=10_000,
        cex_symbol="ETH/USDT",
        lp_start=lp_start,
        lp_end=lp_end,
        lp_pool_label="WETH/USDC",
        window_start=NOW,
        window_end=NOW,
    )
    assert comparison.cex.net_pnl_quote == 1.0
    assert comparison.lp.net_pnl_quote == 7.0
    assert comparison.winner == "lp"
    assert not any("Asset mismatch" in note for note in comparison.mismatch_notes)


def test_compare_handles_missing_books() -> None:
    comparison = compare_books(
        cex_start=None,
        cex_end=None,
        cex_capital=0,
        cex_symbol="ETH/USDC",
        lp_start=None,
        lp_end=None,
        lp_pool_label="WETH/USDC",
    )
    assert comparison.winner == "tie"
    assert comparison.lp.capital_quote == 0 and comparison.cex.return_pct == 0
