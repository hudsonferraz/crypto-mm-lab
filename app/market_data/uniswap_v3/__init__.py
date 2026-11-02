"""Exact integer port of the Uniswap V3 core/periphery math used by the lab.

Everything here is pure (no I/O) and mirrors the Solidity libraries
(FullMath, TickMath, SqrtPriceMath, SwapMath, TickBitmap, Tick, LiquidityAmounts)
closely enough to reproduce on-chain QuoterV2 results to the last unit.
"""
