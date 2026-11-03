"""Rounding to the exchange's trading rules (specs/005 research R4).

Bids round down and asks round up, so rounding never moves a quote toward the book.
Sizes round down to the lot step. Decimal arithmetic avoids float drift at the tick.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from app.models.testnet import RejectReason, Side, SymbolRules


def _dec(value: float) -> Decimal:
    return Decimal(repr(value))


def _quantize(value: float, step: float, rounding: str) -> Decimal:
    step_dec = _dec(step)
    units = (_dec(value) / step_dec).to_integral_value(rounding=rounding)
    return (units * step_dec).normalize()


def round_price(price: float, side: Side, rules: SymbolRules) -> float:
    rounding = ROUND_FLOOR if side == "bid" else ROUND_CEILING
    return float(_quantize(price, rules.tick_size, rounding))


def round_size(size: float, rules: SymbolRules) -> float:
    return float(_quantize(size, rules.step_size, ROUND_FLOOR))


def format_step(value: float, step: float) -> str:
    """Render ``value`` with exactly as many decimals as ``step`` (exchange wire format)."""
    exponent = _dec(step).normalize().as_tuple().exponent
    places = max(0, -int(exponent))
    return f"{_dec(value):.{places}f}"


def check_minimums(price: float, size: float, rules: SymbolRules) -> RejectReason | None:
    if size <= 0 or size < rules.min_qty:
        return "min_size"
    if price * size < rules.min_notional:
        return "min_notional"
    return None


def rules_from_exchange_info(info: dict, symbol: str) -> SymbolRules:
    for entry in info.get("symbols", []):
        if entry.get("symbol") != symbol:
            continue
        filters = {f.get("filterType"): f for f in entry.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        return SymbolRules(
            symbol=symbol,
            tick_size=float(price_filter.get("tickSize", "0.01")),
            step_size=float(lot.get("stepSize", "0.00001")),
            min_qty=float(lot.get("minQty", "0")),
            min_notional=float(notional.get("minNotional", "0")),
        )
    raise ValueError(f"symbol {symbol} not listed on the testnet")
