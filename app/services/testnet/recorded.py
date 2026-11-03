"""The recorded real testnet session shown when no keys are set (specs/005 D2, FR-025).

The recording holds records only (session, orders with life cycle, fills, shadow fills,
ticks). It never holds keys, signatures or URLs; tests/test_testnet_recorded.py checks it.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.analytics.testnet_report import (
    fill_from_dict,
    fill_to_dict,
    order_from_dict,
    order_to_dict,
    session_from_dict,
    session_to_dict,
    shadow_from_dict,
    shadow_to_dict,
    tick_from_dict,
    tick_to_dict,
)
from app.models.testnet import ShadowFill, TestnetFill, TestnetOrder, TestnetSession, TickRecord

FORMAT = 1


@dataclass(frozen=True)
class RecordedSession:
    recorded_at: datetime
    session: TestnetSession
    orders: list[TestnetOrder]
    fills: list[TestnetFill]
    shadow_fills: list[ShadowFill]
    ticks: list[TickRecord]
    last_mid: float | None
    notes: dict

    @property
    def minutes(self) -> float:
        if not self.ticks:
            return 0.0
        return (self.ticks[-1].at - self.ticks[0].at).total_seconds() / 60


def export_session(
    session: TestnetSession,
    orders: list[TestnetOrder],
    fills: list[TestnetFill],
    shadow_fills: list[ShadowFill],
    ticks: list[TickRecord],
    *,
    recorded_at: datetime,
    last_mid: float | None,
    notes: dict | None = None,
) -> dict:
    session_data = session_to_dict(session)
    session_data["source"] = "recorded"
    return {
        "format": FORMAT,
        "recorded_at": recorded_at.isoformat(),
        "session": session_data,
        "orders": [order_to_dict(o) for o in sorted(orders, key=lambda o: o.placed_at)],
        "fills": [fill_to_dict(f) for f in sorted(fills, key=lambda f: f.trade_id)],
        "shadow_fills": [shadow_to_dict(f) for f in shadow_fills],
        "ticks": [tick_to_dict(t) for t in sorted(ticks, key=lambda t: t.seq)],
        "last_mid": last_mid,
        "notes": notes or {},
    }


def parse_recording(data: dict) -> RecordedSession:
    if data.get("format") != FORMAT:
        raise ValueError(f"unsupported recording format {data.get('format')}")
    return RecordedSession(
        recorded_at=datetime.fromisoformat(data["recorded_at"]),
        session=session_from_dict(data["session"]),
        orders=[order_from_dict(o) for o in data["orders"]],
        fills=[fill_from_dict(f) for f in data["fills"]],
        shadow_fills=[shadow_from_dict(f) for f in data["shadow_fills"]],
        ticks=[tick_from_dict(t) for t in data["ticks"]],
        last_mid=data.get("last_mid"),
        notes=data.get("notes", {}),
    )


@lru_cache(maxsize=4)
def load_recording(path: str | None) -> RecordedSession | None:
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    with gzip.open(file, "rt", encoding="utf-8") as handle:
        return parse_recording(json.load(handle))
