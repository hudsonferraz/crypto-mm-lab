"""Paper fill models vs. real testnet fills (specs/005 US2, FR-017 to FR-019, research R8).

Every accepted order has exactly one outcome per fill model: matched (both filled some
size), paper only, real only, or neither, so the four always add up to the orders.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from app.models.testnet import FILL_MODELS, FillComparison, ShadowFill, TestnetFill, TestnetOrder

_EPS = 1e-12


def fee_in_quote(fill: TestnetFill, base_asset: str, quote_asset: str) -> float:
    if fill.fee_asset == quote_asset:
        return fill.fee
    if fill.fee_asset == base_asset:
        return fill.fee * fill.price
    return 0.0  # e.g. BNB discounts; the testnet charges 0 commission today


def _pnl(flows: list[tuple[str, float, float, float]], last_mid: float | None) -> float:
    base = quote = 0.0
    for side, price, size, fee in flows:
        if side == "bid":
            base += size
            quote -= price * size + fee
        else:
            base -= size
            quote += price * size - fee
    return quote + base * (last_mid or 0.0)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compare(
    orders: list[TestnetOrder],
    real_fills: list[TestnetFill],
    shadow_fills: list[ShadowFill],
    *,
    last_mid: float | None,
    base_asset: str = "BTC",
    quote_asset: str = "USDT",
    fill_models: tuple[str, ...] = FILL_MODELS,
) -> list[FillComparison]:
    accepted = {o.client_order_id: o for o in orders if o.accepted}
    real_size: dict[str, float] = defaultdict(float)
    real_notional: dict[str, float] = defaultdict(float)
    real_first: dict[str, float] = {}
    real_flows = []
    for fill in sorted(real_fills, key=lambda f: f.at):
        order = accepted.get(fill.client_order_id)
        if order is None:
            continue
        real_size[fill.client_order_id] += fill.size
        real_notional[fill.client_order_id] += fill.size * fill.price
        if fill.client_order_id not in real_first:
            real_first[fill.client_order_id] = (fill.at - order.acked_at).total_seconds()
        real_flows.append(
            (fill.side, fill.price, fill.size, fee_in_quote(fill, base_asset, quote_asset))
        )
    real_pnl = _pnl(real_flows, last_mid)

    by_model: dict[str, list[ShadowFill]] = defaultdict(list)
    for fill in shadow_fills:
        if fill.client_order_id in accepted:
            by_model[fill.fill_model].append(fill)

    results = []
    n = len(accepted)
    real_filled_ids = {cid for cid, size in real_size.items() if size > _EPS}
    for model in fill_models:
        paper_size: dict[str, float] = defaultdict(float)
        paper_notional: dict[str, float] = defaultdict(float)
        paper_first: dict[str, float] = {}
        flows = []
        for fill in sorted(by_model.get(model, []), key=lambda f: (f.tick_seq, f.at)):
            order = accepted[fill.client_order_id]
            paper_size[fill.client_order_id] += fill.size
            paper_notional[fill.client_order_id] += fill.size * fill.price
            paper_first.setdefault(fill.client_order_id, (fill.at - order.acked_at).total_seconds())
            flows.append((fill.side, fill.price, fill.size, fill.fee))
        paper_ids = {cid for cid, size in paper_size.items() if size > _EPS}
        matched = real_filled_ids & paper_ids
        size_diffs = [paper_size[c] - real_size[c] for c in matched]
        price_diffs = []
        for c in matched:
            real_avg = real_notional[c] / real_size[c]
            paper_avg = paper_notional[c] / paper_size[c]
            price_diffs.append((paper_avg - real_avg) / real_avg * 10_000)
        paper_pnl = _pnl(flows, last_mid)
        results.append(
            FillComparison(
                fill_model=model,
                orders=n,
                real_filled=len(real_filled_ids),
                paper_filled=len(paper_ids),
                matched=len(matched),
                paper_only=len(paper_ids - real_filled_ids),
                real_only=len(real_filled_ids - paper_ids),
                neither=n - len(real_filled_ids | paper_ids),
                real_fill_rate=len(real_filled_ids) / n if n else None,
                paper_fill_rate=len(paper_ids) / n if n else None,
                median_time_to_fill_real_sec=(median(real_first.values()) if real_first else None),
                median_time_to_fill_paper_sec=(
                    median(paper_first.values()) if paper_first else None
                ),
                mean_size_diff=_mean(size_diffs),
                mean_price_diff_bps=_mean(price_diffs),
                real_pnl=real_pnl,
                paper_pnl=paper_pnl,
                pnl_gap=paper_pnl - real_pnl,
            )
        )
    return results
