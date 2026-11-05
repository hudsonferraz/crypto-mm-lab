"""The simulation lab service: per-visitor runs, a bounded queue and limits.

specs/009 D1 (isolation), D5 (limits), FR-006 (stages), FR-013 (off the live loop).
All work runs on the lab's own thread pool; the lab holds no reference to the live
market-making loop, so it cannot change it (FR-010).
"""

import asyncio
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.adapters.cex.ccxt_adapter import short_error
from app.config.settings import Settings
from app.simlab.engine import (
    LAB_FILL_MODELS,
    LAB_STRATEGIES,
    SimSettings,
    SimulationFailed,
    simulate,
)
from app.simlab.explain import (
    DECISIONS,
    FILL_MODEL_HELP,
    FILL_MODEL_LABELS,
    STRATEGY_LABELS,
    summarize,
)
from app.simlab.markets import LAB_EXCHANGES, MarketCatalog, MarketRules, PublicClients
from app.simlab.recorder import RecordingFailed, record_live
from app.simlab.recording import BundleLibrary, Recording

Stage = Literal["waiting", "recording", "simulating", "done", "failed"]
DURATION_RANGE = (30, 120)
SPREAD_RANGE = (1.0, 200.0)
HOUR = 3600.0
_VISITOR_ID = re.compile(r"^[0-9a-f]{32}$")


def normalize_visitor_id(raw: str | None) -> str | None:
    """32 hex digits, dashes allowed (a UUID); None when malformed (R4)."""
    if not raw:
        return None
    value = raw.strip().lower().replace("-", "")
    return value if _VISITOR_ID.match(value) else None


class LabRefused(Exception):
    def __init__(
        self,
        status: int,
        limit: str,
        message: str,
        *,
        retry_after_sec: float | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.limit = limit
        self.message = message
        self.retry_after_sec = retry_after_sec
        self.run_id = run_id

    def detail(self) -> dict:
        detail: dict = {"limit": self.limit, "message": self.message}
        if self.retry_after_sec is not None:
            detail["retry_after_sec"] = max(1, round(self.retry_after_sec))
        if self.run_id is not None:
            detail["run_id"] = self.run_id
        return detail


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    source: Literal["live", "bundled"]
    strategy: str
    spread_bps: float
    fill_model: str
    exchange: str | None = None
    symbol: str | None = None
    bundle_id: str | None = None
    duration_sec: int | None = None

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(slots=True)
class SimulationRun:
    run_id: str
    visitor_id: str
    request: SimulationRequest
    created_at: float
    stage: Stage = "waiting"
    started_at: float | None = None
    finished_at: float | None = None
    seconds_left: float | None = None
    error: str | None = None
    result: dict | None = None
    label: str = ""
    rules: MarketRules | None = None
    recording: Recording | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.stage in ("waiting", "recording", "simulating")


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat() if epoch is not None else None


class SimLab:
    def __init__(
        self,
        settings: Settings,
        *,
        clients: PublicClients | None = None,
        library: BundleLibrary | None = None,
        clock: Callable[[], float] = time.time,
        recorder: Callable[..., Recording] = record_live,
    ) -> None:
        self._settings = settings
        self._clients = clients or PublicClients()
        self._catalog = MarketCatalog(self._clients, cache_sec=settings.lab_market_cache_sec)
        self._library = library or BundleLibrary()
        self._clock = clock
        self._recorder = recorder
        self._runs: dict[str, SimulationRun] = {}
        self._waiting: deque[str] = deque()
        self._history: dict[tuple[str, str], deque[float]] = {}
        self._cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._queue: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task] = []
        self._stopping = False

    # ---------------------------------------------------------------- lifecycle

    def _ensure_started(self) -> None:
        if self._queue is not None:
            return
        workers = self._settings.lab_concurrent_recordings
        # R5: a pool of its own, so lab work never queues ahead of a live tick.
        self._executor = ThreadPoolExecutor(max_workers=workers + 1, thread_name_prefix="simlab")
        self._queue = asyncio.Queue()
        self._workers = [asyncio.create_task(self._worker()) for _ in range(workers)]

    async def stop(self) -> None:
        self._stopping = True
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._workers = []
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._queue = None

    async def _in_pool(self, function, *args):
        self._ensure_started()
        return await asyncio.get_running_loop().run_in_executor(self._executor, function, *args)

    # ---------------------------------------------------------------- options

    def _options_sync(self) -> dict:
        exchanges = []
        for exchange in LAB_EXCHANGES.values():
            pairs = self._catalog.pairs(exchange.id)
            exchanges.append(
                {
                    "id": exchange.id,
                    "label": exchange.label,
                    "interval_sec": exchange.interval_sec,
                    "pairs": [rules.to_dict() for rules in pairs],
                }
            )
        settings = self._settings
        return {
            "exchanges": exchanges,
            "bundles": [info.to_public_dict() for info in self._library.list()],
            "strategies": [{"id": s, "label": STRATEGY_LABELS[s]} for s in LAB_STRATEGIES],
            "fill_models": [
                {"id": m, "label": FILL_MODEL_LABELS[m], "help": FILL_MODEL_HELP[m]}
                for m in LAB_FILL_MODELS
            ],
            "limits": {
                "duration_sec": list(DURATION_RANGE),
                "spread_bps": list(SPREAD_RANGE),
                "concurrent_recordings": settings.lab_concurrent_recordings,
                "queue_size": settings.lab_queue_size,
                "runs_per_hour": settings.lab_runs_per_hour,
                "bundled_runs_per_hour": settings.lab_bundled_runs_per_hour,
                "keep_hours": settings.lab_keep_hours,
                "runs_kept": settings.lab_runs_kept,
            },
            "decisions": DECISIONS,
        }

    async def options(self) -> dict:
        return await self._in_pool(self._options_sync)

    async def warm(self) -> None:
        """Load the market lists once at startup, so the first visitor does not wait."""
        try:
            await self.options()
        except Exception:
            pass  # the catalog falls back per exchange; a visitor's request retries

    # ---------------------------------------------------------------- validation

    def _validate(self, request: SimulationRequest) -> tuple[MarketRules | None, str]:
        if request.strategy not in LAB_STRATEGIES:
            raise LabRefused(422, "invalid", f"strategy must be one of {list(LAB_STRATEGIES)}")
        if request.fill_model not in LAB_FILL_MODELS:
            raise LabRefused(422, "invalid", f"fill_model must be one of {list(LAB_FILL_MODELS)}")
        low, high = SPREAD_RANGE
        if not low <= request.spread_bps <= high:
            raise LabRefused(422, "invalid", f"spread_bps must be between {low:g} and {high:g}")
        if request.source == "bundled":
            info = self._library.get(request.bundle_id or "")
            if info is None:
                raise LabRefused(422, "invalid", "unknown bundled recording")
            return None, info.label
        if request.source != "live":
            raise LabRefused(422, "invalid", "source must be live or bundled")
        low_d, high_d = DURATION_RANGE
        if request.duration_sec is None or not low_d <= request.duration_sec <= high_d:
            raise LabRefused(
                422, "invalid", f"duration_sec must be between {low_d} and {high_d} seconds"
            )
        if request.exchange not in LAB_EXCHANGES:
            raise LabRefused(422, "invalid", f"exchange must be one of {list(LAB_EXCHANGES)}")
        rules = self._catalog.rules(request.exchange, request.symbol or "")
        if rules is None:
            # FR-024: anything else never reaches the exchange.
            raise LabRefused(422, "invalid", "pair is not offered on this exchange")
        label = f"{LAB_EXCHANGES[request.exchange].label} {request.symbol}, live"
        return rules, label

    # ---------------------------------------------------------------- limits

    def _prune(self, now: float) -> None:
        keep_sec = self._settings.lab_keep_hours * HOUR
        for run_id, run in list(self._runs.items()):
            if not run.active and now - (run.finished_at or run.created_at) > keep_sec:
                del self._runs[run_id]
        for key, stamps in list(self._history.items()):
            while stamps and now - stamps[0] > HOUR:
                stamps.popleft()
            if not stamps:
                del self._history[key]

    def _check_hourly(self, keys: list[tuple[str, str]], limit: int, kind: str, now: float):
        for key in keys:
            stamps = self._history.get(key)
            if stamps and len(stamps) >= limit:
                who = "this browser" if key[0].startswith("visitor") else "this network address"
                raise LabRefused(
                    429,
                    "per_hour",
                    f"{who} reached {limit} {kind} in the last hour",
                    retry_after_sec=stamps[0] + HOUR - now,
                )

    def _keep_newest(self, visitor_id: str) -> None:
        own = sorted(
            (run for run in self._runs.values() if run.visitor_id == visitor_id),
            key=lambda run: run.created_at,
        )
        excess = len(own) - self._settings.lab_runs_kept
        for run in own:
            if excess <= 0:
                break
            if not run.active:
                del self._runs[run.run_id]
                excess -= 1

    # ---------------------------------------------------------------- runs

    async def submit(
        self, visitor_id: str, address: str, request: SimulationRequest
    ) -> SimulationRun:
        rules, label = await self._in_pool(self._validate, request)
        now = self._clock()
        live = request.source == "live"
        with self._lock:
            self._prune(now)
            active = next(
                (r for r in self._runs.values() if r.visitor_id == visitor_id and r.active), None
            )
            if active is not None:
                raise LabRefused(
                    409,
                    "active_run",
                    "you already have a run waiting or running; it must finish first",
                    run_id=active.run_id,
                )
            kind = "live" if live else "bundled"
            keys = [(f"visitor-{kind}", visitor_id), (f"address-{kind}", address)]
            limit = (
                self._settings.lab_runs_per_hour
                if live
                else self._settings.lab_bundled_runs_per_hour
            )
            self._check_hourly(keys, limit, "live recordings" if live else "bundled runs", now)
            if live and len(self._waiting) >= self._settings.lab_queue_size:
                raise LabRefused(
                    429,
                    "queue_full",
                    "the waiting line is full; try again in a couple of minutes",
                    retry_after_sec=120,
                )
            run = SimulationRun(
                run_id=secrets.token_hex(16),
                visitor_id=visitor_id,
                request=request,
                created_at=now,
                label=label,
                rules=rules,
            )
            self._runs[run.run_id] = run
            for key in keys:
                self._history.setdefault(key, deque()).append(now)
            self._keep_newest(visitor_id)
            if live:
                self._waiting.append(run.run_id)

        self._ensure_started()
        if live:
            assert self._queue is not None
            self._queue.put_nowait(run.run_id)
        else:
            await self._finish_bundled(run)
        return run

    async def _finish_bundled(self, run: SimulationRun) -> None:
        request = run.request
        key = (request.bundle_id, request.strategy, request.spread_bps, request.fill_model)
        run.started_at = self._clock()
        cached = self._cache.get(key)
        if cached is not None:
            # FR-008: identical settings on a bundled recording give the same result.
            self._complete(run, cached)
            return
        run.stage = "simulating"
        try:
            recording = await self._in_pool(self._library.load, request.bundle_id)
            result = await self._in_pool(self._simulate_dict, recording, request)
        except Exception as error:
            self._fail(run, error)
            return
        self._cache[key] = result
        self._complete(run, result)

    def _simulate_dict(self, recording: Recording, request: SimulationRequest) -> dict:
        settings = SimSettings(request.strategy, request.spread_bps, request.fill_model)  # type: ignore[arg-type]
        result = simulate(recording, settings)
        quote_ccy = recording.symbol.split("/")[1]
        result.summary = summarize(result, settings, quote_ccy)
        payload = result.to_dict()
        payload["recording"] = {
            "exchange": recording.exchange,
            "symbol": recording.symbol,
            "source": recording.source,
            "label": recording.label,
            "started_at": recording.started_at.isoformat(),
            "price_step": recording.rules.price_step,
            "amount_step": recording.rules.amount_step,
        }
        return payload

    def _complete(self, run: SimulationRun, result: dict) -> None:
        run.result = result
        run.stage = "done"
        run.seconds_left = None
        run.finished_at = self._clock()

    def _fail(self, run: SimulationRun, error: Exception) -> None:
        if isinstance(error, (SimulationFailed, RecordingFailed)):
            run.error = str(error)
        else:
            run.error = f"the run failed ({short_error(error)})"
        run.stage = "failed"
        run.seconds_left = None
        run.finished_at = self._clock()

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            run_id = await self._queue.get()
            run = self._runs.get(run_id)
            with self._lock:
                if run_id in self._waiting:
                    self._waiting.remove(run_id)
            if run is None or not run.active:
                continue
            await self._record_and_simulate(run)

    async def _record_and_simulate(self, run: SimulationRun) -> None:
        request = run.request
        run.stage = "recording"
        run.started_at = self._clock()
        run.seconds_left = float(request.duration_sec or 0)

        def progress(seconds_left: float) -> None:
            run.seconds_left = seconds_left

        try:
            recording = await self._in_pool(
                lambda: self._recorder(
                    self._clients,
                    request.exchange,
                    request.symbol,
                    run.rules,
                    float(request.duration_sec or 0),
                    on_progress=progress,
                    should_stop=lambda: self._stopping,
                )
            )
            run.stage = "simulating"
            run.seconds_left = None
            result = await self._in_pool(self._simulate_dict, recording, request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fail(run, error)
            return
        self._complete(run, result)

    # ---------------------------------------------------------------- reading

    def _summary(self, run: SimulationRun, *, with_result: bool) -> dict:
        position = None
        if run.stage == "waiting":
            with self._lock:
                waiting = list(self._waiting)
            position = waiting.index(run.run_id) + 1 if run.run_id in waiting else None
        payload = {
            "run_id": run.run_id,
            "label": run.label,
            "request": run.request.to_dict(),
            "stage": run.stage,
            "queue_position": position,
            "seconds_left": None if run.seconds_left is None else round(run.seconds_left, 1),
            "created_at": _iso(run.created_at),
            "finished_at": _iso(run.finished_at),
            "error": run.error,
        }
        if run.result is not None:
            result = run.result
            if with_result:
                payload["result"] = result
            else:
                payload["headline"] = {
                    key: result[key]
                    for key in (
                        "final_value",
                        "hold_value",
                        "edge_vs_hold",
                        "fees",
                        "fill_count",
                        "duration_sec",
                    )
                }
        return payload

    def list_runs(self, visitor_id: str) -> list[dict]:
        with self._lock:
            self._prune(self._clock())
            own = [run for run in self._runs.values() if run.visitor_id == visitor_id]
        own.sort(key=lambda run: run.created_at, reverse=True)
        return [self._summary(run, with_result=False) for run in own]

    def get_run(self, visitor_id: str, run_id: str) -> dict | None:
        with self._lock:
            self._prune(self._clock())
            run = self._runs.get(run_id)
        # D1: another visitor's run looks exactly like a missing one.
        if run is None or run.visitor_id != visitor_id:
            return None
        return self._summary(run, with_result=True)
