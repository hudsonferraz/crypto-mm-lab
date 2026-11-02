"""Read/write recorded V3 data: quote vectors (pool state + tick window + quoter
outputs at one block) and per-block pool observations (CSV)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.market_data.uniswap_v3.pricing import sqrt_price_x96_to_price
from app.models.v3 import PoolMetadata, TickInfo, TickWindow, V3PoolSnapshot

DEFAULT_QUOTE_VECTORS_PATH = Path("tests/fixtures/v3_quote_vectors.json")
DEFAULT_OBSERVATIONS_PATH = Path("tests/fixtures/v3_pool_observations.csv")

OBSERVATION_COLUMNS = (
    "block_number",
    "block_timestamp",
    "sqrt_price_x96",
    "tick",
    "liquidity",
    "fee_growth_global0_x128",
    "fee_growth_global1_x128",
    "base_fee_wei",
    "is_stale",
)


@dataclass(frozen=True, slots=True)
class QuoteVector:
    zero_for_one: bool
    amount_in: int
    quoter_amount_out: int
    quoter_sqrt_price_after_x96: int
    quoter_ticks_crossed: int


@dataclass(frozen=True, slots=True)
class QuoteVectorFixture:
    snapshot: V3PoolSnapshot
    vectors: tuple[QuoteVector, ...]


def _metadata_to_dict(metadata: PoolMetadata) -> dict:
    return {
        "pool_address": metadata.pool_address,
        "token0": metadata.token0,
        "token1": metadata.token1,
        "token0_decimals": metadata.token0_decimals,
        "token1_decimals": metadata.token1_decimals,
        "fee_pips": metadata.fee_pips,
        "tick_spacing": metadata.tick_spacing,
        "base_is_token0": metadata.base_is_token0,
    }


def _tick_to_dict(info: TickInfo) -> dict:
    return {
        "tick": info.tick,
        "liquidity_gross": str(info.liquidity_gross),
        "liquidity_net": str(info.liquidity_net),
        "fee_growth_outside0_x128": str(info.fee_growth_outside0_x128),
        "fee_growth_outside1_x128": str(info.fee_growth_outside1_x128),
        "initialized": info.initialized,
    }


def _tick_from_dict(raw: dict) -> TickInfo:
    return TickInfo(
        tick=int(raw["tick"]),
        liquidity_gross=int(raw["liquidity_gross"]),
        liquidity_net=int(raw["liquidity_net"]),
        fee_growth_outside0_x128=int(raw["fee_growth_outside0_x128"]),
        fee_growth_outside1_x128=int(raw["fee_growth_outside1_x128"]),
        initialized=bool(raw["initialized"]),
    )


def dump_quote_vectors(path: Path, snapshot: V3PoolSnapshot, vectors: list[QuoteVector]) -> None:
    if snapshot.window is None:
        raise ValueError("snapshot has no tick window to record")
    window = snapshot.window
    payload = {
        "description": "Uniswap V3 pool state, tick window and QuoterV2 outputs at one block",
        "metadata": _metadata_to_dict(snapshot.metadata),
        "block_number": snapshot.block_number,
        "block_timestamp": snapshot.block_timestamp.isoformat(),
        "sqrt_price_x96": str(snapshot.sqrt_price_x96),
        "tick": snapshot.tick,
        "liquidity": str(snapshot.liquidity),
        "fee_growth_global0_x128": str(snapshot.fee_growth_global0_x128),
        "fee_growth_global1_x128": str(snapshot.fee_growth_global1_x128),
        "base_fee_wei": None if snapshot.base_fee_wei is None else str(snapshot.base_fee_wei),
        "window": {
            "block_number": window.block_number,
            "tick_spacing": window.tick_spacing,
            "min_word": window.min_word,
            "max_word": window.max_word,
            "bitmap_words": {str(k): str(v) for k, v in sorted(window.bitmap_words.items())},
            "ticks": [_tick_to_dict(info) for _, info in sorted(window.ticks.items())],
        },
        "vectors": [
            {
                "zero_for_one": v.zero_for_one,
                "amount_in": str(v.amount_in),
                "quoter_amount_out": str(v.quoter_amount_out),
                "quoter_sqrt_price_after_x96": str(v.quoter_sqrt_price_after_x96),
                "quoter_ticks_crossed": v.quoter_ticks_crossed,
            }
            for v in vectors
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def load_quote_vectors(path: Path = DEFAULT_QUOTE_VECTORS_PATH) -> QuoteVectorFixture:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    metadata = PoolMetadata(**raw["metadata"])
    w = raw["window"]
    window = TickWindow(
        block_number=int(w["block_number"]),
        tick_spacing=int(w["tick_spacing"]),
        bitmap_words={int(k): int(v) for k, v in w["bitmap_words"].items()},
        ticks={int(t["tick"]): _tick_from_dict(t) for t in w["ticks"]},
        min_word=int(w["min_word"]),
        max_word=int(w["max_word"]),
    )
    sqrt_price = int(raw["sqrt_price_x96"])
    snapshot = V3PoolSnapshot(
        metadata=metadata,
        sqrt_price_x96=sqrt_price,
        tick=int(raw["tick"]),
        liquidity=int(raw["liquidity"]),
        fee_growth_global0_x128=int(raw["fee_growth_global0_x128"]),
        fee_growth_global1_x128=int(raw["fee_growth_global1_x128"]),
        block_number=int(raw["block_number"]),
        block_timestamp=datetime.fromisoformat(raw["block_timestamp"]),
        price=sqrt_price_x96_to_price(
            sqrt_price,
            base_is_token0=metadata.base_is_token0,
            token0_decimals=metadata.token0_decimals,
            token1_decimals=metadata.token1_decimals,
        ),
        base_fee_wei=None if raw.get("base_fee_wei") is None else int(raw["base_fee_wei"]),
        window=window,
        data_source="fixture",
    )
    vectors = tuple(
        QuoteVector(
            zero_for_one=bool(v["zero_for_one"]),
            amount_in=int(v["amount_in"]),
            quoter_amount_out=int(v["quoter_amount_out"]),
            quoter_sqrt_price_after_x96=int(v["quoter_sqrt_price_after_x96"]),
            quoter_ticks_crossed=int(v["quoter_ticks_crossed"]),
        )
        for v in raw["vectors"]
    )
    return QuoteVectorFixture(snapshot=snapshot, vectors=vectors)


def observation_row(snapshot: V3PoolSnapshot) -> dict[str, str]:
    return {
        "block_number": str(snapshot.block_number),
        "block_timestamp": snapshot.block_timestamp.isoformat(),
        "sqrt_price_x96": str(snapshot.sqrt_price_x96),
        "tick": str(snapshot.tick),
        "liquidity": str(snapshot.liquidity),
        "fee_growth_global0_x128": str(snapshot.fee_growth_global0_x128),
        "fee_growth_global1_x128": str(snapshot.fee_growth_global1_x128),
        "base_fee_wei": "" if snapshot.base_fee_wei is None else str(snapshot.base_fee_wei),
        "is_stale": "true" if snapshot.is_stale else "false",
    }


def snapshot_from_observation(
    row: dict, metadata: PoolMetadata, *, window: TickWindow | None = None
) -> V3PoolSnapshot:
    sqrt_price = int(row["sqrt_price_x96"])
    timestamp = row["block_timestamp"]
    block_timestamp = (
        timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp))
    )
    base_fee = row.get("base_fee_wei")
    return V3PoolSnapshot(
        metadata=metadata,
        sqrt_price_x96=sqrt_price,
        tick=int(row["tick"]),
        liquidity=int(row["liquidity"]),
        fee_growth_global0_x128=int(row["fee_growth_global0_x128"]),
        fee_growth_global1_x128=int(row["fee_growth_global1_x128"]),
        block_number=int(row["block_number"]),
        block_timestamp=block_timestamp.astimezone(UTC),
        price=sqrt_price_x96_to_price(
            sqrt_price,
            base_is_token0=metadata.base_is_token0,
            token0_decimals=metadata.token0_decimals,
            token1_decimals=metadata.token1_decimals,
        ),
        base_fee_wei=int(base_fee) if base_fee not in (None, "") else None,
        window=window,
        is_stale=str(row.get("is_stale", "false")).lower() in {"1", "true", "yes"},
        data_source="fixture",
    )


def write_observations(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OBSERVATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def load_observations(path: Path = DEFAULT_OBSERVATIONS_PATH) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"observation fixture is empty: {path}")
    return rows
