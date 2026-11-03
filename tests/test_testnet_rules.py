"""Rounding to the testnet's trading rules (specs/005 research R4)."""

from app.models.testnet import SymbolRules
from app.services.testnet.rules import (
    check_minimums,
    format_step,
    round_price,
    round_size,
    rules_from_exchange_info,
)
from tests.testnet_helpers import EXCHANGE_INFO

RULES = SymbolRules("BTCUSDT", tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5)


def test_bids_round_down_and_asks_round_up() -> None:
    assert round_price(77_650.129, "bid", RULES) == 77_650.12
    assert round_price(77_650.121, "ask", RULES) == 77_650.13
    assert round_price(77_650.12, "bid", RULES) == 77_650.12  # already on the tick
    assert round_price(77_650.12, "ask", RULES) == 77_650.12


def test_sizes_round_down_to_the_step() -> None:
    assert round_size(0.0012345, RULES) == 0.00123
    assert round_size(0.001, RULES) == 0.001


def test_minimums() -> None:
    assert check_minimums(77_000, 0.000001, RULES) == "min_size"
    assert check_minimums(77_000, 0.00005, RULES) == "min_notional"
    assert check_minimums(77_000, 0.001, RULES) is None


def test_wire_format_uses_the_step_decimals() -> None:
    assert format_step(77_650.1, 0.01) == "77650.10"
    assert format_step(0.001, 0.00001) == "0.00100"


def test_rules_from_exchange_info() -> None:
    rules = rules_from_exchange_info(EXCHANGE_INFO, "BTCUSDT")
    assert rules == RULES
