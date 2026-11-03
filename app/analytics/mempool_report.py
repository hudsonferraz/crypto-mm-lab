"""Serialization, summary statistics and plain-language explanations for the mempool
watcher (specs/004-mempool-watcher/contracts/api.md, FR-022 to FR-024).

Raw amounts are stored and served as strings (they can exceed 2^63). Live and
replay records are summarized separately, never combined (FR-017).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from statistics import median

from app.models.mempool import (
    BlockVisibility,
    LegOutcome,
    MempoolSummary,
    ModeChange,
    SwapLeg,
    SwapOutcome,
    WatchedSwap,
    WatcherStatus,
)

EXPLANATIONS: dict[str, str] = {
    "D1": (
        "Most swaps on these pools are never seen here. Since 2023 most wallets and bots "
        "send DEX trades through private relays straight to block builders, skipping the "
        "public mempool. The 'seen before inclusion' rate measures how much is still public."
    ),
    "D2": (
        "Live mode follows pending transactions on a free public endpoint. Because most swaps "
        "are never public, every block's swaps are also replayed: predicted from the state "
        "before the block, as if they had been seen pending, and checked against their own "
        "events. If the endpoint stops sharing pending transactions, replay is all that runs. "
        "Replay results are labeled and never mixed with live ones."
    ),
    "D3": (
        "A pending swap is predicted against the latest block, as if it were the next trade. "
        "Its real position is unknown in advance, so any miss is split into 'math error' "
        "(our pool math vs the chain, expected 0) and 'ordering effect' (trades that landed "
        "ahead of it)."
    ),
    "D4": (
        "Swaps sent directly to the pools or through the standard Uniswap routers are decoded. "
        "Other contracts that touch the pools (aggregators, bots) are counted as 'seen, not "
        "decoded' so the coverage stays honest."
    ),
    "D5": (
        "A predicted opportunity counts as 'likely back-run' when a later transaction in the "
        "same block trades the arbitrage across both pools and the gap ends the block back "
        "below the fees. Intent cannot be proven from chain data, so this is a stated rule."
    ),
    "D6": (
        "The watcher runs beside the market-making loop, never inside it, with a fixed backlog "
        "and a fixed number of optional calls per block so it cannot slow quoting or exhaust "
        "the free endpoint."
    ),
    "D7": (
        "Read-only: nothing is signed, sent or acted on, and no key exists. Only the two "
        "existing WETH/USDC pools on mainnet are covered."
    ),
}

FIELD_EXPLANATIONS: dict[str, str] = {
    "visible_share": "Share of swap transactions on our pools that were seen pending first (D1).",
    "math_error_bps": "Our prediction from the true pre-trade state vs the chain; 0 = exact (D3).",
    "ordering_effect_bps": "How far trades that landed first moved the result (D3).",
    "decode_coverage": "Decoded swaps / all pending transactions seen touching our pools (D4).",
    "capture_rate": "Opportunities that appeared and were likely back-run in the same block (D5).",
    "opportunity_precision": "Flagged 'opens an opportunity' swaps whose opportunity appeared.",
    "inclusion_rate": "Watched swaps that landed in a block (vs replaced or never included).",
    "latency_blocks": "Blocks between first seeing a swap and its inclusion.",
    "latency_seconds": "Seconds between first seeing a swap and its block's timestamp.",
}

_RAW_LEG = (
    "amount_specified",
    "limit",
    "predicted_amount_in",
    "predicted_amount_out",
    "predicted_sqrt_price_after_x96",
    "sqrt_price_limit_x96",
    "fixed_amount_out",
)
_RAW_OUTCOME = ("actual_amount_in", "actual_amount_out", "amount_out_error_raw")


def _s(value: int | None) -> str | None:
    return None if value is None else str(value)


def _i(value) -> int | None:
    return None if value is None else int(value)


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _pdt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


# --- storage form -----------------------------------------------------------------


def leg_to_dict(leg: SwapLeg) -> dict:
    data = asdict(leg)
    for name in _RAW_LEG:
        data[name] = _s(data[name])
    if leg.predicted_reserves_after is not None:
        data["predicted_reserves_after"] = [str(v) for v in leg.predicted_reserves_after]
    data["flags"] = list(leg.flags)
    return data


def leg_from_dict(data: dict) -> SwapLeg:
    values = dict(data)
    for name in _RAW_LEG:
        values[name] = _i(values.get(name))
    reserves = values.get("predicted_reserves_after")
    values["predicted_reserves_after"] = (
        (int(reserves[0]), int(reserves[1])) if reserves is not None else None
    )
    values["flags"] = tuple(values.get("flags", ()))
    return SwapLeg(**values)


def leg_outcome_to_dict(leg: LegOutcome) -> dict:
    data = asdict(leg)
    for name in _RAW_OUTCOME:
        data[name] = str(data[name])
    return data


def leg_outcome_from_dict(data: dict) -> LegOutcome:
    values = dict(data)
    for name in _RAW_OUTCOME:
        values[name] = int(values[name])
    return LegOutcome(**values)


def outcome_to_dict(outcome: SwapOutcome) -> dict:
    return {
        "block_number": outcome.block_number,
        "tx_index": outcome.tx_index,
        "blocks_waited": outcome.blocks_waited,
        "seconds_waited": outcome.seconds_waited,
        "legs": [leg_outcome_to_dict(leg) for leg in outcome.legs],
        "appeared": outcome.appeared,
        "likely_backrun": outcome.likely_backrun,
        "backrun_tx_hash": outcome.backrun_tx_hash,
        "resolved_at": _dt(outcome.resolved_at),
    }


def outcome_from_dict(data: dict) -> SwapOutcome:
    return SwapOutcome(
        block_number=data["block_number"],
        tx_index=data["tx_index"],
        blocks_waited=data["blocks_waited"],
        seconds_waited=data["seconds_waited"],
        legs=tuple(leg_outcome_from_dict(leg) for leg in data["legs"]),
        appeared=data["appeared"],
        likely_backrun=data["likely_backrun"],
        backrun_tx_hash=data["backrun_tx_hash"],
        resolved_at=_pdt(data["resolved_at"]),
    )


def watched_to_dict(swap: WatchedSwap) -> dict:
    return {
        "tx_hash": swap.tx_hash,
        "mode": swap.mode,
        "route": swap.route,
        "sender": swap.sender,
        "nonce": swap.nonce,
        "first_seen_at": _dt(swap.first_seen_at),
        "seen_head_block": swap.seen_head_block,
        "deadline": swap.deadline,
        "legs": [leg_to_dict(leg) for leg in swap.legs],
        "gap_before_bps": swap.gap_before_bps,
        "gap_after_bps": swap.gap_after_bps,
        "scan_net_before_quote": swap.scan_net_before_quote,
        "scan_net_after_quote": swap.scan_net_after_quote,
        "opportunity": swap.opportunity,
        "opportunity_direction": swap.opportunity_direction,
        "status": swap.status,
        "outcome": outcome_to_dict(swap.outcome) if swap.outcome is not None else None,
    }


def watched_from_dict(data: dict) -> WatchedSwap:
    values = dict(data)
    values["first_seen_at"] = _pdt(values["first_seen_at"])
    values["legs"] = tuple(leg_from_dict(leg) for leg in values["legs"])
    values["outcome"] = outcome_from_dict(values["outcome"]) if values.get("outcome") else None
    return WatchedSwap(**values)


def block_to_dict(block: BlockVisibility) -> dict:
    data = asdict(block)
    data["timestamp"] = _dt(block.timestamp)
    return data


def block_from_dict(data: dict) -> BlockVisibility:
    values = dict(data)
    values["timestamp"] = _pdt(values["timestamp"])
    return BlockVisibility(**values)


def mode_change_to_dict(change: ModeChange | None) -> dict | None:
    if change is None:
        return None
    return {
        "at": _dt(change.at),
        "from_mode": change.from_mode,
        "to_mode": change.to_mode,
        "source": change.source,
        "reason": change.reason,
    }


# --- API form ---------------------------------------------------------------------


def swap_note(swap: WatchedSwap) -> str:
    """One plain-language sentence for a record (FR-024)."""
    outcome = swap.outcome
    flags = {flag for leg in swap.legs for flag in leg.flags}
    if swap.status == "pending":
        if "would_revert" in flags:
            return "Waiting. At the latest block its price limit would fail, so it should revert."
        return "Waiting to be included; prediction is against the latest block."
    if swap.status == "replaced":
        return "The sender replaced it with another transaction (same nonce)."
    if swap.status == "not_included":
        return "Not included within the resolve window."
    if swap.status == "included_reverted":
        return "Included, but it reverted on-chain, so it is left out of price accuracy."
    if swap.status == "included_no_pool_event":
        return "Included without trading on our pools (its route changed); left out of accuracy."
    if swap.status == "verification_mismatch":
        return "Included, but its pool events did not match the decoded legs; left out."
    if swap.status == "no_state":
        return "Could not be verified: no pool state for its block (e.g. watcher restart)."
    if outcome is None:
        return ""
    math = max((leg.math_error_bps for leg in outcome.legs), default=0.0)
    ordering = max((leg.ordering_effect_bps for leg in outcome.legs), default=0.0)
    where = (
        "Replayed from its block"
        if swap.mode == "replay"
        else f"Landed {outcome.blocks_waited} block(s) later"
    )
    math_text = "math exact" if math == 0 else f"math off by {math:.4f} bps"
    text = f"{where} at position {outcome.tx_index}; {math_text}"
    text += (
        f"; trades ahead of it moved the price {ordering:.2f} bps."
        if ordering > 0
        else "; nothing ahead of it moved the price."
    )
    if swap.opportunity == "opens":
        if outcome.likely_backrun:
            text += " The opportunity it opened appeared and was likely back-run in the same block."
        elif outcome.appeared:
            text += " The opportunity it opened appeared and was still open after its trade."
        else:
            text += " The opportunity it was predicted to open did not appear."
    return text


def watched_payload(swap: WatchedSwap) -> dict:
    data = watched_to_dict(swap)
    for leg in data["legs"]:
        for name in _RAW_LEG:
            leg[f"{name}_raw"] = leg.pop(name)
    if data["outcome"] is not None:
        for leg in data["outcome"]["legs"]:
            for name in _RAW_OUTCOME:
                leg[f"{name}_raw" if not name.endswith("_raw") else name] = leg.pop(name)
    data.pop("sender", None)
    data["note"] = swap_note(swap)
    return data


def block_payload(block: BlockVisibility) -> dict:
    data = block_to_dict(block)
    data["visible_share"] = block.visible_share
    return data


def status_payload(status: WatcherStatus) -> dict:
    return {
        "enabled": status.enabled,
        "mode": status.mode,
        "state": status.state,
        "reason": status.reason,
        "backend": status.backend,
        "source": status.source,
        "counters": dict(status.counters),
        "last_block": status.last_block,
        "last_switch": mode_change_to_dict(status.last_switch),
        "started_at": _dt(status.started_at),
        "replay_alongside": status.replay_alongside,
    }


# --- summary ----------------------------------------------------------------------


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(
    mode: str,
    swaps: list[WatchedSwap],
    blocks: list[BlockVisibility],
) -> MempoolSummary | None:
    swaps = [s for s in swaps if s.mode == mode]
    blocks = [b for b in blocks if b.mode == mode]
    if not swaps and not blocks:
        return None
    final = [s for s in swaps if s.is_final]
    status_counts = dict(Counter(s.status for s in swaps))
    landed = [
        s for s in final if s.status in ("included", "included_reverted", "included_no_pool_event")
    ]
    included = [s for s in final if s.status == "included" and s.outcome is not None]
    legs = [
        leg
        for s in included
        for leg, predicted in zip(s.outcome.legs, s.legs, strict=False)
        if "beyond_range" not in predicted.flags
    ]
    math = [leg.math_error_bps for leg in legs]
    ordering = [leg.ordering_effect_bps for leg in legs]
    latency_blocks = [
        float(s.outcome.blocks_waited) for s in included if s.outcome.blocks_waited is not None
    ]
    latency_seconds = [
        s.outcome.seconds_waited for s in included if s.outcome.seconds_waited is not None
    ]
    flagged = [s for s in final if s.opportunity == "opens"]
    appeared = [s for s in flagged if s.outcome is not None and s.outcome.appeared]
    backruns = [s for s in appeared if s.outcome.likely_backrun]
    total_pool_swaps = sum(b.pool_swap_txs for b in blocks)
    seen = sum(b.seen_pending for b in blocks)
    times = [s.first_seen_at for s in swaps] + [b.timestamp for b in blocks]
    decoded = sum(1 for s in swaps if s.route != "replay")
    not_decoded = sum(b.not_decoded for b in blocks)
    return MempoolSummary(
        mode=mode,
        window_start=min(times) if times else None,
        window_end=max(times) if times else None,
        watched=len(swaps),
        status_counts=status_counts,
        inclusion_rate=_rate(len(landed), len(final)),
        latency_blocks_p50=median(latency_blocks) if latency_blocks else None,
        latency_blocks_p95=_percentile(latency_blocks, 0.95),
        latency_seconds_p50=median(latency_seconds) if latency_seconds else None,
        latency_seconds_p95=_percentile(latency_seconds, 0.95),
        math_error_bps_p50=median(math) if math else None,
        math_error_bps_p95=_percentile(math, 0.95),
        exact_rate=_rate(sum(1 for leg in legs if leg.exact), len(legs)),
        ordering_effect_bps_p50=median(ordering) if ordering else None,
        ordering_effect_bps_p95=_percentile(ordering, 0.95),
        visible_share=_rate(seen, total_pool_swaps) if mode == "live" else None,
        blocks=len(blocks),
        decode_coverage=_rate(decoded, decoded + not_decoded) if mode == "live" else None,
        opportunities_flagged=len(flagged),
        opportunities_appeared=len(appeared),
        opportunity_precision=_rate(len(appeared), len(flagged)),
        backruns=len(backruns),
        capture_rate=_rate(len(backruns), len(appeared)),
    )


def summary_payload(summary: MempoolSummary | None) -> dict | None:
    if summary is None:
        return None
    data = asdict(summary)
    data["window_start"] = _dt(summary.window_start)
    data["window_end"] = _dt(summary.window_end)
    return data
