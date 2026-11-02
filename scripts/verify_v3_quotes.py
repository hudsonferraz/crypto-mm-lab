"""Check the exact V3 swap simulator against Uniswap's on-chain QuoterV2.

Loads the pool state and tick window at one block, then quotes log-spaced
exact-input sizes in both directions with QuoterV2 (eth_call at the same block)
and with the local simulator. Any difference in amount out or final sqrt price
fails the run. Uses the free public RPC by default (ETH_RPC_URL); no API key.

    python scripts/verify_v3_quotes.py --samples 24
    python scripts/verify_v3_quotes.py --record tests/fixtures/v3_quote_vectors.json
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.dex.abis import QUOTER_V2_ADDRESS, QUOTER_V2_EXACT_INPUT_SINGLE  # noqa: E402
from app.adapters.dex.multicall import Call, Multicall, Web3Transport  # noqa: E402
from app.adapters.dex.v3_fixtures import QuoteVector, dump_quote_vectors  # noqa: E402
from app.adapters.dex.v3_pool_adapter import V3PoolAdapter  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.market_data.uniswap_v3.swap_simulator import (  # noqa: E402
    pool_state_from_snapshot,
    simulate_exact_input,
)


def log_spaced(low: float, high: float, count: int) -> list[float]:
    step = (math.log10(high) - math.log10(low)) / (count - 1)
    return [10 ** (math.log10(low) + i * step) for i in range(count)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pool", default=None, help="V3 pool address (default V3_POOL_ADDRESS)")
    parser.add_argument("--samples", type=int, default=24, help="sizes per direction")
    parser.add_argument("--window-words", type=int, default=3)
    parser.add_argument("--record", type=Path, default=None, help="write vectors JSON here")
    args = parser.parse_args(argv)

    settings = get_settings()
    transport = Web3Transport(settings.eth_rpc_url, timeout_sec=30)
    adapter = V3PoolAdapter(
        transport,
        args.pool or settings.v3_pool_address,
        window_words=args.window_words,
        max_retries=3,
    )
    snapshot = adapter.read_snapshot()
    if not snapshot.window_is_current or snapshot.window is None or snapshot.is_incomplete:
        print("could not load a complete, block-consistent window; retry", file=sys.stderr)
        return 2

    metadata = snapshot.metadata
    state = pool_state_from_snapshot(snapshot)
    base_units = 10**metadata.base_decimals
    quote_units = 10**metadata.quote_decimals
    price = snapshot.price
    print(
        f"pool {metadata.pool_address} fee {metadata.fee_tier_bps:g} bps "
        f"block {snapshot.block_number} price {price:,.4f} "
        f"window words [{snapshot.window.min_word}, {snapshot.window.max_word}] "
        f"initialized ticks {len(snapshot.window.ticks)}"
    )

    # sell base (WETH in) and buy base (USDC in), from dust to multi-word swaps
    cases: list[tuple[bool, int, str]] = []
    for amount in log_spaced(1e-6, 20_000, args.samples):
        cases.append((metadata.base_is_token0, int(amount * base_units), "sell_base"))
    for amount in log_spaced(1e-2, 20_000 * price, args.samples):
        cases.append((not metadata.base_is_token0, int(amount * quote_units), "buy_base"))

    multicall = Multicall(transport)
    quoter_calls = []
    for zero_for_one, amount_in, _side in cases:
        token_in, token_out = (
            (metadata.token0, metadata.token1)
            if zero_for_one
            else (metadata.token1, metadata.token0)
        )
        signature, outputs = QUOTER_V2_EXACT_INPUT_SINGLE
        quoter_calls.append(
            Call(
                target=QUOTER_V2_ADDRESS,
                signature=signature,
                args=((token_in, token_out, amount_in, metadata.fee_pips, 0),),
                output_types=outputs,
            )
        )
    results = multicall.try_aggregate(quoter_calls, snapshot.block_number, chunk_size=12)

    vectors: list[QuoteVector] = []
    compared = mismatches = window_limited = 0
    print(f"{'side':9} {'amount_in':>28} {'quoter_out':>28} {'diff':>6} {'ticks':>5} note")
    for (zero_for_one, amount_in, side), result in zip(cases, results, strict=True):
        if not result.success:
            print(f"{side:9} {amount_in:>28} {'quoter reverted':>28}")
            continue
        q_out, q_sqrt_after, q_ticks, _gas = result.value
        quote = simulate_exact_input(
            state, snapshot.window, zero_for_one=zero_for_one, amount_in=amount_in
        )
        if quote.limit_reason == "window_exhausted":
            window_limited += 1
            print(f"{side:9} {amount_in:>28} {q_out:>28} {'-':>6} {'-':>5} beyond loaded window")
            continue
        diff = quote.amount_out - int(q_out)
        price_match = quote.sqrt_price_after_x96 == int(q_sqrt_after)
        compared += 1
        if diff != 0 or not price_match:
            mismatches += 1
        note = "" if price_match else "sqrt price after differs"
        print(f"{side:9} {amount_in:>28} {q_out:>28} {diff:>6} {quote.ticks_crossed:>5} {note}")
        vectors.append(
            QuoteVector(
                zero_for_one=zero_for_one,
                amount_in=amount_in,
                quoter_amount_out=int(q_out),
                quoter_sqrt_price_after_x96=int(q_sqrt_after),
                quoter_ticks_crossed=int(q_ticks),
            )
        )

    print(
        f"\ncompared {compared}, mismatches {mismatches}, beyond window {window_limited} "
        f"(block {snapshot.block_number})"
    )
    if args.record is not None and mismatches == 0:
        dump_quote_vectors(args.record, snapshot, vectors)
        print(f"recorded {len(vectors)} vectors to {args.record}")
    return 0 if mismatches == 0 and compared > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
