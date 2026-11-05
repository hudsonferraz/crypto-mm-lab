"""Recorded books and public trades for one pair (specs/009 data-model)."""

import gzip
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.models.domain import OrderBookLevel, OrderBookSnapshot
from app.simlab.markets import MarketRules

BUNDLE_DIR = Path(__file__).resolve().parent / "recordings"
FORMAT = 1
MIN_SNAPSHOTS = 10


@dataclass(frozen=True, slots=True)
class TradePrint:
    timestamp: datetime
    price: float
    amount: float
    side: str | None = None


@dataclass(slots=True)
class Recording:
    exchange: str
    symbol: str
    rules: MarketRules
    started_at: datetime
    ended_at: datetime
    source: Literal["live", "bundled"] = "live"
    label: str = ""
    snapshots: list[OrderBookSnapshot] = field(default_factory=list)
    trades: list[TradePrint] = field(default_factory=list)

    @property
    def duration_sec(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def usable_snapshots(self) -> int:
        return sum(1 for snapshot in self.snapshots if not snapshot.is_stale)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def recording_to_dict(recording: Recording) -> dict:
    return {
        "format": FORMAT,
        "exchange": recording.exchange,
        "symbol": recording.symbol,
        "label": recording.label,
        "rules": recording.rules.to_dict(),
        "started_at": recording.started_at.isoformat(),
        "ended_at": recording.ended_at.isoformat(),
        # Compact rows: [ms, [[price, size], ...], [[price, size], ...]]
        "snapshots": [
            [
                _ms(snapshot.timestamp),
                [[level.price, level.size] for level in snapshot.bids],
                [[level.price, level.size] for level in snapshot.asks],
            ]
            for snapshot in recording.snapshots
        ],
        "trades": [
            [_ms(trade.timestamp), trade.price, trade.amount, trade.side]
            for trade in recording.trades
        ],
    }


def recording_from_dict(data: dict, *, source: Literal["live", "bundled"] = "bundled") -> Recording:
    if data.get("format") != FORMAT:
        raise ValueError(f"unsupported recording format: {data.get('format')}")
    symbol = data["symbol"]
    snapshots = [
        OrderBookSnapshot(
            symbol=symbol,
            bids=tuple(OrderBookLevel(float(p), float(s)) for p, s in bids),
            asks=tuple(OrderBookLevel(float(p), float(s)) for p, s in asks),
            timestamp=_from_ms(ms),
        )
        for ms, bids, asks in data["snapshots"]
    ]
    trades = [
        TradePrint(_from_ms(ms), float(price), float(amount), side)
        for ms, price, amount, side in data["trades"]
    ]
    return Recording(
        exchange=data["exchange"],
        symbol=symbol,
        rules=MarketRules.from_dict(data["rules"]),
        started_at=datetime.fromisoformat(data["started_at"]),
        ended_at=datetime.fromisoformat(data["ended_at"]),
        source=source,
        label=data.get("label", ""),
        snapshots=snapshots,
        trades=trades,
    )


def save_recording(recording: Recording, path: Path) -> None:
    payload = json.dumps(recording_to_dict(recording), separators=(",", ":"))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(payload)


def load_recording(path: Path) -> Recording:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return recording_from_dict(json.load(handle))


@dataclass(frozen=True, slots=True)
class BundleInfo:
    id: str
    label: str
    exchange: str
    symbol: str
    recorded_at: str
    duration_sec: float
    snapshot_count: int
    trade_count: int
    file: str

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "recorded_at": self.recorded_at,
            "duration_sec": self.duration_sec,
            "snapshot_count": self.snapshot_count,
            "trade_count": self.trade_count,
        }


def bundle_info_for(bundle_id: str, recording: Recording, file_name: str) -> BundleInfo:
    return BundleInfo(
        id=bundle_id,
        label=recording.label,
        exchange=recording.exchange,
        symbol=recording.symbol,
        recorded_at=recording.started_at.isoformat(),
        duration_sec=round(recording.duration_sec, 1),
        snapshot_count=len(recording.snapshots),
        trade_count=len(recording.trades),
        file=file_name,
    )


class BundleLibrary:
    """Bundled real recordings listed in `index.json` (specs/009 D2, R9)."""

    def __init__(self, directory: Path = BUNDLE_DIR) -> None:
        self._directory = directory
        self._cache: dict[str, Recording] = {}
        index_path = directory / "index.json"
        entries = json.loads(index_path.read_text("utf-8")) if index_path.exists() else []
        self._bundles = {entry["id"]: BundleInfo(**entry) for entry in entries}

    def list(self) -> list[BundleInfo]:
        return list(self._bundles.values())

    def get(self, bundle_id: str) -> BundleInfo | None:
        return self._bundles.get(bundle_id)

    def load(self, bundle_id: str) -> Recording:
        if bundle_id not in self._cache:
            info = self._bundles[bundle_id]
            self._cache[bundle_id] = load_recording(self._directory / info.file)
        return self._cache[bundle_id]


def write_bundle_index(directory: Path, infos: list[BundleInfo]) -> None:
    rows = [{**info.to_public_dict(), "file": info.file} for info in infos]
    (directory / "index.json").write_text(json.dumps(rows, indent=2) + "\n", "utf-8")
