"""MempoolWatcher: follows pending swaps on our two pools and verifies them per block.

Runs beside the market-making loop, never inside it (D6): its own asyncio tasks,
its own HTTP client, a bounded queue and a per-block call budget. CPU work
(simulation, scanning) runs in worker threads. Read-only throughout (D7): nothing is
signed or sent and no key exists.

Tasks:
- intake: pending source chain (websocket full objects -> HTTP filter), cheap
  filter + decode, queue;
- predict: resolve multi-hop amounts, predict against the latest tracked block;
- blocks: poll the head, roll pool state forward per block, verify watched swaps,
  replay blocks when live data is unavailable, persist, metrics, retention.

The step methods (``on_pending``, ``drain``, ``process_block``) are public so tests
drive the watcher deterministically from recorded data.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import structlog
from eth_abi import decode, encode

from app.adapters.dex.abis import (
    QUOTER_V2_ADDRESS,
    QUOTER_V2_EXACT_INPUT,
    V2_ROUTER02_ADDRESS,
    V2_ROUTER_GET_AMOUNTS_OUT,
)
from app.adapters.dex.json_rpc import (
    BudgetExceeded,
    CallBudget,
    JsonRpcClient,
    RpcClient,
    RpcError,
    RpcRefused,
    hex_bytes,
    hex_int,
)
from app.adapters.dex.multicall import selector
from app.adapters.dex.pending_sources import (
    FilterPendingSource,
    PendingSource,
    WebSocketPendingSource,
)
from app.adapters.dex.pool_events import ALL_TOPICS, parse_logs
from app.config.redaction import PUBLIC_RPC_HOSTS
from app.config.settings import Settings
from app.models.mempool import (
    COUNTER_NAMES,
    DecodedLeg,
    DecodedSwap,
    Mode,
    ModeChange,
    PendingTx,
    SourceName,
    SwapLeg,
    WatchedSwap,
    WatcherState,
    WatcherStatus,
)
from app.observability import metrics as prom
from app.services.mempool.anchor import AnchorLoader, get_header, get_pool_logs
from app.services.mempool.pool_state import BlockState, PoolStateTracker, ReorgDetected, StateGap
from app.services.mempool.predictor import ScanParams, predict, scan_state
from app.services.mempool.replay import replay_block
from app.services.mempool.router_decoder import (
    DecoderConfig,
    NotDecoded,
    decode_transaction,
    decoded_targets,
    is_candidate,
)
from app.services.mempool.verifier import block_visibility, resolve_unincluded, verify_included

logger = structlog.get_logger(__name__)

REFUSED_REASON = "endpoint does not share pending transactions"
_RETENTION_EVERY_SEC = 3600.0
_MAX_REORG_DEPTH = 12
_MAX_CATCH_UP = 16


class BlockNotReady(RuntimeError):
    """The endpoint does not have the block yet (load-balanced head)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def endpoint_label(url: str | None) -> str | None:
    """Backend label only; the URL itself is never exposed (FR-001)."""
    if not url:
        return None
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if host in {"127.0.0.1", "localhost", "0.0.0.0", "anvil"}:
        return "local-fork"
    return "public-rpc" if host in PUBLIC_RPC_HOSTS else "custom-rpc"


def default_sources(settings: Settings, rpc: RpcClient) -> list[PendingSource]:
    sources: list[PendingSource] = []
    if settings.mempool_ws_url:
        sources.append(WebSocketPendingSource(settings.mempool_ws_url))
    sources.append(FilterPendingSource(rpc))
    return sources


class MempoolWatcher:
    def __init__(
        self,
        settings: Settings,
        repository,
        *,
        rpc: RpcClient | None = None,
        sources: list[PendingSource] | None = None,
        anchor_loader: AnchorLoader | None = None,
        now: Callable[[], datetime] = _utcnow,
        head_poll_sec: float = 2.0,
        head_lag_blocks: int = 1,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._own_rpc = rpc is None
        self._rpc: RpcClient = rpc or JsonRpcClient(
            settings.mempool_http_url, timeout_sec=settings.mempool_rpc_timeout_sec
        )
        self._sources = sources if sources is not None else default_sources(settings, self._rpc)
        self._decoder = DecoderConfig(
            v2_pair=settings.dex_pool_address.lower(), v3_pool=settings.v3_pool_address.lower()
        )
        self._targets = decoded_targets(self._decoder)
        self._anchor = anchor_loader or AnchorLoader(
            self._rpc,
            v2_pair=settings.dex_pool_address,
            v3_pool=settings.v3_pool_address,
            window_words=settings.mempool_v3_window_words,
        )
        self._tracker: PoolStateTracker | None = None
        self._params = ScanParams(
            reference_size_quote=settings.arb_reference_size_quote,
            gas_units=settings.arb_gas_units,
            priority_fee_gwei=settings.arb_priority_fee_gwei,
            fallback_gas_price_gwei=settings.gas_price_gwei,
        )
        self._budget = CallBudget(settings.mempool_call_budget_per_block)
        self._queue: asyncio.Queue[tuple[PendingTx, DecodedSwap]] = asyncio.Queue(
            maxsize=settings.mempool_queue_size
        )
        self._now = now
        self._head_poll_sec = head_poll_sec
        self._head_lag = head_lag_blocks
        self._metrics = settings.metrics_enabled

        self._watched: dict[str, WatchedSwap] = {}
        self._by_nonce: dict[tuple[str, int], str] = {}
        self._recorded: OrderedDict[str, None] = OrderedDict()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._resolved_by_block: dict[int, list[WatchedSwap]] = {}
        self._not_decoded_since_block = 0
        self._visible_window: deque[tuple[int, int]] = deque(maxlen=100)
        self._scan_cache: tuple[int, object] | None = None
        self.counters: Counter[str] = Counter({name: 0 for name in COUNTER_NAMES})

        forced = settings.mempool_mode
        self._mode: Mode = "replay" if forced == "replay" else "live"
        self._source: SourceName | None = "replay" if forced == "replay" else None
        self._state: WatcherState = "starting"
        self._reason: str | None = None
        self._last_switch: ModeChange | None = None
        self._last_pending_at: datetime | None = None
        self._live_refused = False
        self._last_block: int | None = None
        self._block_errors = 0
        self._started_at: datetime | None = None
        self._last_retention = 0.0
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # --- status -----------------------------------------------------------------

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def tracker(self) -> PoolStateTracker | None:
        return self._tracker

    @property
    def pending(self) -> list[WatchedSwap]:
        return sorted(self._watched.values(), key=lambda s: s.first_seen_at, reverse=True)

    @property
    def status(self) -> WatcherStatus:
        url = (
            self._settings.mempool_ws_url
            if self._source == "ws"
            else self._settings.mempool_http_url
        )
        counters = dict(self.counters)
        counters["pending_watched"] = len(self._watched)
        return WatcherStatus(
            enabled=True,
            mode=self._mode,
            state=self._state,
            reason=self._reason,
            backend=endpoint_label(url),
            source=self._source,
            counters=counters,
            last_block=self._last_block,
            last_switch=self._last_switch,
            started_at=self._started_at,
        )

    def _set_mode(self, mode: Mode, source: SourceName | None, reason: str) -> None:
        source = source or ("replay" if mode == "replay" else self._source or "ws")
        changed = mode != self._mode or self._last_switch is None
        self._source = source
        if not changed:
            return
        from_mode = None if self._last_switch is None else self._mode
        change = ModeChange(self._now(), from_mode, mode, source, reason)
        self._mode = mode
        self._last_switch = change
        logger.info("mempool_mode", mode=mode, source=source, reason=reason)
        try:
            self._repository.save_mempool_mode_change(change)
        except Exception as error:  # storage must never stop watching
            logger.warning("mempool_mode_persist_failed", error=type(error).__name__)
        if self._metrics:
            prom.MEMPOOL_LIVE.set(1 if mode == "live" else 0)

    # --- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = self._now()
        await asyncio.to_thread(self._close_stale_records)
        if self._settings.mempool_mode == "replay":
            self._set_mode("replay", "replay", "forced by MEMPOOL_MODE=replay")
        self._tasks = [asyncio.create_task(self._block_loop(), name="mempool-blocks")]
        if self._settings.mempool_mode != "replay":
            self._tasks.append(asyncio.create_task(self._intake_loop(), name="mempool-intake"))
            self._tasks.append(asyncio.create_task(self._predict_loop(), name="mempool-predict"))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self._own_rpc and isinstance(self._rpc, JsonRpcClient):
            await self._rpc.aclose()

    def _close_stale_records(self) -> None:
        """Pending records from a previous run cannot be verified any more."""
        try:
            stale = self._repository.get_mempool_swaps(limit=500, status="pending")
        except Exception:
            return
        if stale:
            self._repository.upsert_mempool_swaps([replace(s, status="no_state") for s in stale])

    # --- intake --------------------------------------------------------------------

    def _remember(self, store: OrderedDict[str, None], key: str, limit: int) -> None:
        store[key] = None
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)

    async def on_pending(self, tx: PendingTx, source: SourceName | None = None) -> None:
        self.counters["seen"] += 1
        if self._metrics:
            prom.MEMPOOL_TXS_SEEN.labels(source=source or self._source or "ws").inc()
        self._remember(self._seen, tx.tx_hash, self._settings.mempool_seen_hashes)
        self._last_pending_at = self._now()
        if self._settings.mempool_mode == "auto" and self._mode == "replay":
            self._set_mode("live", source, "pending feed available again")
        if self._state in ("unavailable", "starting") and self._last_block is not None:
            self._state, self._reason = "ok", None

        if not is_candidate(tx, self._decoder, self._targets):
            return
        if tx.tx_hash in self._watched or tx.tx_hash in self._recorded:
            return
        config = self._tracker.config if self._tracker is not None else None
        result = decode_transaction(
            tx,
            self._decoder,
            v2_usdc_is_token0=config.v2_usdc_is_token0 if config else True,
            v3_usdc_is_token0=config.v3_usdc_is_token0 if config else True,
        )
        if result is None:
            return
        self.counters["kept"] += 1
        if isinstance(result, NotDecoded):
            self.counters["not_decoded"] += 1
            self._not_decoded_since_block += 1
            if self._metrics:
                prom.MEMPOOL_NOT_DECODED.inc()
            return
        self.counters["decoded"] += 1
        self._replace_same_nonce(tx)
        self._remember(self._recorded, tx.tx_hash, self._settings.mempool_seen_hashes)
        try:
            self._queue.put_nowait((tx, result))
        except asyncio.QueueFull:
            self.counters["dropped_queue"] += 1
            if self._metrics:
                prom.MEMPOOL_DROPPED.labels(reason="queue_full").inc()

    def _replace_same_nonce(self, tx: PendingTx) -> None:
        if tx.sender is None or tx.nonce is None:
            return
        old_hash = self._by_nonce.get((tx.sender, tx.nonce))
        if old_hash is None or old_hash == tx.tx_hash:
            return
        old = self._watched.pop(old_hash, None)
        if old is not None:
            replaced = resolve_unincluded(old, old.seen_head_block, old.nonce + 1, self._now())
            self._persist_swaps([replaced])
            self._count_outcome(replaced)

    async def drain(self) -> None:
        """Predict everything queued (tests; the predict task does this continuously)."""
        while not self._queue.empty():
            tx, decoded = self._queue.get_nowait()
            await self._predict_item(tx, decoded)

    async def _predict_loop(self) -> None:
        while self._running:
            tx, decoded = await self._queue.get()
            try:
                await self._predict_item(tx, decoded)
            except Exception as error:
                logger.warning("mempool_predict_failed", error=type(error).__name__)

    async def _resolve_upstream(self, leg: DecodedLeg) -> DecodedLeg | None:
        if leg.amount_specified is not None:
            return leg
        kind, path, amount_in = leg.upstream_path
        self._budget.spend()
        if kind == "v3":
            data = selector(QUOTER_V2_EXACT_INPUT[0]) + encode(
                ["bytes", "uint256"], [path, amount_in]
            )
            target = QUOTER_V2_ADDRESS
        else:
            data = selector(V2_ROUTER_GET_AMOUNTS_OUT[0]) + encode(
                ["uint256", "address[]"], [amount_in, list(path)]
            )
            target = V2_ROUTER02_ADDRESS
        raw = await self._rpc.call(
            "eth_call", [{"to": target.lower(), "data": "0x" + data.hex()}, "latest"]
        )
        if kind == "v3":
            amount = int(decode(list(QUOTER_V2_EXACT_INPUT[1]), hex_bytes(raw))[0])
        else:
            amount = int(decode(["uint256[]"], hex_bytes(raw))[0][-1])
        return replace(leg, amount_specified=amount, upstream_path=None)

    async def _scan_before(self, state) -> object | None:
        if self._scan_cache is not None and self._scan_cache[0] == state.block_number:
            return self._scan_cache[1]
        candidate = await asyncio.to_thread(scan_state, self._tracker.config, state, self._params)
        self._scan_cache = (state.block_number, candidate)
        return candidate

    async def _predict_item(self, tx: PendingTx, decoded: DecodedSwap) -> None:
        state = self._tracker.latest if self._tracker is not None else None
        waited = 0.0
        while state is None and waited < 15.0 and self._running:
            await asyncio.sleep(0.5)
            waited += 0.5
            state = self._tracker.latest if self._tracker is not None else None
        if state is None:
            # No pool state to predict against: recorded, never guessed (edge case "no state").
            await asyncio.to_thread(self._persist_swaps, [self._unpredicted(tx, decoded, 0)])
            return
        legs: list[DecodedLeg] = []
        try:
            for leg in decoded.legs:
                legs.append(await self._resolve_upstream(leg))
        except BudgetExceeded:
            self.counters["dropped_budget"] += 1
            if self._metrics:
                prom.MEMPOOL_DROPPED.labels(reason="budget").inc()
            return
        except (RpcError, ValueError) as error:
            logger.info("mempool_upstream_quote_failed", error=type(error).__name__)
            unpredicted = self._unpredicted(tx, decoded, state.block_number)
            await asyncio.to_thread(self._persist_swaps, [unpredicted])
            return
        scan_before = await self._scan_before(state)
        prediction = await asyncio.to_thread(
            predict,
            self._tracker.config,
            state,
            tuple(legs),
            self._params,
            deadline=decoded.deadline,
            scan_before=scan_before,
        )
        watched = WatchedSwap(
            tx_hash=tx.tx_hash,
            mode="live",
            route=decoded.route,
            sender=tx.sender,
            nonce=tx.nonce,
            first_seen_at=tx.seen_at,
            seen_head_block=state.block_number,
            deadline=decoded.deadline,
            legs=prediction.legs,
            gap_before_bps=prediction.gap_before_bps,
            gap_after_bps=prediction.gap_after_bps,
            scan_net_before_quote=prediction.scan_net_before_quote,
            scan_net_after_quote=prediction.scan_net_after_quote,
            opportunity=prediction.opportunity,
            opportunity_direction=prediction.opportunity_direction,
            status="pending",
        )
        self._watched[tx.tx_hash] = watched
        if tx.sender is not None and tx.nonce is not None:
            self._by_nonce[(tx.sender, tx.nonce)] = tx.tx_hash
        await asyncio.to_thread(self._persist_swaps, [watched])
        if self._metrics:
            prom.MEMPOOL_SWAPS.labels(mode="live", route=decoded.route).inc()
            if watched.opportunity == "opens":
                prom.MEMPOOL_OPPORTUNITIES.labels(mode="live", result="flagged").inc()

    def _unpredicted(self, tx: PendingTx, decoded: DecodedSwap, head: int) -> WatchedSwap:
        legs = tuple(
            SwapLeg(
                pool=leg.pool,
                direction=leg.direction,
                kind=leg.kind,
                amount_specified=leg.amount_specified or 0,
                limit=leg.limit,
                predicted_amount_in=None,
                predicted_amount_out=None,
                price_before=None,
                predicted_price_after=None,
                flags=leg.flags,
            )
            for leg in decoded.legs
        )
        return WatchedSwap(
            tx_hash=tx.tx_hash,
            mode="live",
            route=decoded.route,
            sender=tx.sender,
            nonce=tx.nonce,
            first_seen_at=tx.seen_at,
            seen_head_block=head,
            deadline=decoded.deadline,
            legs=legs,
            gap_before_bps=None,
            gap_after_bps=None,
            scan_net_before_quote=None,
            scan_net_after_quote=None,
            opportunity=None,
            opportunity_direction=None,
            status="no_state",
        )

    async def _intake_loop(self) -> None:
        failures = 0
        index = 0
        refused: set[int] = set()
        while self._running:
            if len(refused) >= len(self._sources):
                self._live_refused = True
                if self._settings.mempool_mode == "live":
                    self._state, self._reason = "unavailable", REFUSED_REASON
                elif self._mode != "replay":
                    self._set_mode("replay", "replay", REFUSED_REASON)
                await asyncio.sleep(self._settings.mempool_live_grace_sec)
                refused.clear()  # retry live in the background (R11)
                continue
            source = self._sources[index % len(self._sources)]
            if index % len(self._sources) in refused:
                index += 1
                continue
            try:
                async for tx in source.stream():
                    failures = 0
                    self._live_refused = False
                    if self._mode == "live" and self._source != source.name:
                        self._source = source.name
                    await self.on_pending(tx, source.name)
                    if not self._running:
                        return
                raise RpcError("pending stream ended")
            except RpcRefused as error:
                logger.info("mempool_source_refused", source=source.name, error=str(error)[:120])
                refused.add(index % len(self._sources))
                index += 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                logger.info("mempool_source_error", source=source.name, error=str(error)[:120])
                if failures >= 3:
                    index += 1
                    failures = 0
                await asyncio.sleep(min(30.0, 2.0**failures))

    # --- blocks --------------------------------------------------------------------

    async def _anchor_at(self, number: int) -> None:
        header = await get_header(self._rpc, number)
        if header is None:
            raise BlockNotReady(f"block {number} not available")
        fresh = await self._anchor.load(header)
        if self._tracker is None:
            self._tracker = PoolStateTracker(self._anchor.config)
        issues = self._tracker.anchor(fresh)
        self.counters["reanchors"] += 1
        if issues is not None:
            result = "mismatch" if issues else "match"
            if issues:
                self.counters["state_mismatches"] += 1
                logger.warning("mempool_state_mismatch", block=number, issues=issues)
            if self._metrics:
                prom.MEMPOOL_STATE_CHECKS.labels(result=result).inc()

    def _restore_from(self, block_number: int) -> None:
        """A reorg dropped blocks >= block_number: their live swaps are pending again."""
        for number in [n for n in self._resolved_by_block if n >= block_number]:
            for original in self._resolved_by_block.pop(number):
                if original.mode == "live":
                    self._watched[original.tx_hash] = original

    async def _apply(self, number: int, depth: int = 0) -> BlockState:
        header = await get_header(self._rpc, number)
        if header is None:
            raise BlockNotReady(f"block {number} not available")
        tracker = self._tracker
        if tracker is None or tracker.needs_reanchor(self._settings.mempool_reanchor_blocks):
            await self._anchor_at(number - 1)
            tracker = self._tracker
        logs = await get_pool_logs(
            self._rpc, header.hash, [self._decoder.v2_pair, self._decoder.v3_pool], ALL_TOPICS
        )
        events = parse_logs(
            logs or [], v2_pair=self._decoder.v2_pair, v3_pool=self._decoder.v3_pool
        )
        try:
            return tracker.apply_block(header, events)
        except ReorgDetected:
            self.counters["reorgs"] += 1
            if depth >= _MAX_REORG_DEPTH or tracker.hash_at(number - 2) is None:
                await self._anchor_at(number - 1)
                self._restore_from(number - 1)
                return tracker.apply_block(header, events)
            tracker.rewind_to(number - 2)
            self._restore_from(number - 1)
            parent = await self._apply(number - 1, depth + 1)
            await self._after_block(parent)
            return tracker.apply_block(header, events)
        except StateGap:
            await self._anchor_at(number - 1)
            return tracker.apply_block(header, events)

    async def process_block(self, number: int) -> BlockState:
        started = time.perf_counter()
        block = await self._apply(number)
        await self._after_block(block)
        if self._metrics:
            prom.MEMPOOL_BLOCK_SECONDS.observe(time.perf_counter() - started)
        return block

    async def _after_block(self, block: BlockState) -> None:
        config = self._tracker.config
        number = block.header.number
        now = self._now()
        self._budget.reset()
        index_of = {tx_hash: i for i, tx_hash in enumerate(block.header.tx_hashes)}
        resolved: list[WatchedSwap] = []
        originals: list[WatchedSwap] = []
        for watched in list(self._watched.values()):
            tx_index = index_of.get(watched.tx_hash)
            if tx_index is not None:
                receipt_status = None
                if not block.swaps_of(watched.tx_hash):
                    receipt_status = await self._receipt_status(watched.tx_hash)
                updated = await asyncio.to_thread(
                    verify_included,
                    watched,
                    block,
                    tx_index,
                    config,
                    self._params,
                    receipt_status=receipt_status,
                    now=now,
                )
            elif number - watched.seen_head_block >= self._settings.mempool_resolve_blocks:
                nonce = await self._nonce(watched, number)
                if nonce is _DEFER:
                    continue
                updated = resolve_unincluded(watched, number, nonce, now)
            else:
                continue
            originals.append(watched)
            resolved.append(updated)
            del self._watched[watched.tx_hash]
            if watched.sender is not None and watched.nonce is not None:
                self._by_nonce.pop((watched.sender, watched.nonce), None)

        if self._mode == "replay":
            live_hashes = {s.tx_hash for s in resolved}
            records = await asyncio.to_thread(replay_block, config, block, self._params, now=now)
            replayed = [r for r in records if r.tx_hash not in live_hashes]
            resolved.extend(replayed)
            if self._metrics:
                for record in replayed:
                    prom.MEMPOOL_SWAPS.labels(mode="replay", route="replay").inc()

        visibility = await asyncio.to_thread(
            block_visibility,
            config,
            block,
            self._mode,
            seen_hashes=self._seen if self._mode == "live" else None,
            watched_included=sum(1 for s in resolved if s.status == "included"),
            backruns=sum(1 for s in resolved if s.outcome and s.outcome.likely_backrun),
            params=self._params,
        )
        visibility = replace(visibility, not_decoded=self._not_decoded_since_block)
        self._not_decoded_since_block = 0
        await asyncio.to_thread(self._persist_block, resolved, visibility)

        self._resolved_by_block[number] = originals
        for old in [n for n in self._resolved_by_block if n < number - 16]:
            del self._resolved_by_block[old]
        for swap in resolved:
            self._count_outcome(swap)
        if self._metrics and visibility.mode == "live" and visibility.pool_swap_txs:
            self._visible_window.append((visibility.seen_pending, visibility.pool_swap_txs))
            seen = sum(a for a, _ in self._visible_window)
            total = sum(b for _, b in self._visible_window)
            prom.MEMPOOL_VISIBLE_SHARE.set(seen / total if total else 0)
        self._last_block = number
        self.counters["blocks_processed"] += 1

    async def _receipt_status(self, tx_hash: str) -> int | None:
        try:
            self._budget.spend()
            receipt = await self._rpc.call("eth_getTransactionReceipt", [tx_hash])
        except (BudgetExceeded, RpcError):
            return None
        return hex_int(receipt["status"]) if receipt and receipt.get("status") else None

    async def _nonce(self, watched: WatchedSwap, number: int):
        if watched.sender is None:
            return None
        overdue = number - watched.seen_head_block >= self._settings.mempool_resolve_blocks + 3
        try:
            self._budget.spend()
            value = await self._rpc.call("eth_getTransactionCount", [watched.sender, hex(number)])
            return hex_int(value)
        except BudgetExceeded:
            if overdue:
                self.counters["dropped_budget"] += 1
                return None
            return _DEFER
        except RpcError:
            return None if overdue else _DEFER

    def _persist_swaps(self, swaps: list[WatchedSwap]) -> None:
        try:
            self._repository.upsert_mempool_swaps(swaps)
        except Exception as error:
            logger.warning("mempool_persist_failed", error=type(error).__name__)

    def _persist_block(self, swaps: list[WatchedSwap], visibility) -> None:
        self._persist_swaps(swaps)
        try:
            self._repository.save_mempool_block(visibility)
        except Exception as error:
            logger.warning("mempool_block_persist_failed", error=type(error).__name__)

    def _count_outcome(self, swap: WatchedSwap) -> None:
        if not self._metrics:
            return
        prom.MEMPOOL_OUTCOMES.labels(mode=swap.mode, status=swap.status).inc()
        outcome = swap.outcome
        if outcome is None or swap.status != "included":
            return
        for leg in outcome.legs:
            prom.MEMPOOL_MATH_ERROR_BPS.labels(mode=swap.mode).observe(leg.math_error_bps)
            prom.MEMPOOL_ORDERING_EFFECT_BPS.labels(mode=swap.mode).observe(leg.ordering_effect_bps)
        if swap.mode == "live" and outcome.blocks_waited is not None:
            prom.MEMPOOL_INCLUSION_BLOCKS.observe(outcome.blocks_waited)
        if outcome.seconds_waited is not None:
            prom.MEMPOOL_INCLUSION_SECONDS.observe(outcome.seconds_waited)
        if outcome.appeared:
            prom.MEMPOOL_OPPORTUNITIES.labels(mode=swap.mode, result="appeared").inc()
        if outcome.likely_backrun:
            prom.MEMPOOL_OPPORTUNITIES.labels(mode=swap.mode, result="backrun").inc()

    def _check_grace(self) -> None:
        if self._settings.mempool_mode != "auto" or self._mode != "live":
            return
        reference = self._last_pending_at or self._started_at
        if reference is None:
            return
        idle = (self._now() - reference).total_seconds()
        if idle > self._settings.mempool_live_grace_sec:
            self._set_mode(
                "replay", "replay", f"no pending transactions for {int(idle)} s; replaying blocks"
            )

    def _retention(self) -> None:
        cutoff = self._now() - timedelta(days=self._settings.mempool_retention_days)
        try:
            self._repository.purge_mempool(cutoff)
        except Exception as error:
            logger.warning("mempool_retention_failed", error=type(error).__name__)

    async def _block_loop(self) -> None:
        while self._running:
            try:
                head = hex_int(await self._rpc.call("eth_blockNumber", [])) - self._head_lag
                if self._last_block is None or head - self._last_block > _MAX_CATCH_UP:
                    self._last_block = head - 1  # start (or resume) at the head
                for number in range(self._last_block + 1, head + 1):
                    try:
                        await self.process_block(number)
                    except BlockNotReady:
                        break
                self._block_errors = 0
                if self._state in ("starting", "degraded", "unavailable") and not (
                    self._settings.mempool_mode == "live" and self._live_refused
                ):
                    self._state, self._reason = "ok", None
                if self._last_switch is None and self._mode == "live" and self._last_pending_at:
                    self._set_mode("live", self._source, "pending feed connected")
                self._check_grace()
                if time.monotonic() - self._last_retention > _RETENTION_EVERY_SEC:
                    self._last_retention = time.monotonic()
                    await asyncio.to_thread(self._retention)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._block_errors += 1
                self._state = "degraded" if self._block_errors < 5 else "unavailable"
                self._reason = f"block reads failing: {type(error).__name__}"
                logger.warning("mempool_block_failed", error=str(error)[:200])
                await asyncio.sleep(min(30.0, 2.0**self._block_errors))
                continue
            await asyncio.sleep(self._head_poll_sec)


_DEFER = object()


def disabled_status(reason: str = "MEMPOOL_ENABLED=false") -> WatcherStatus:
    return WatcherStatus(
        enabled=False,
        mode=None,
        state="disabled",
        reason=reason,
        backend=None,
        source=None,
        counters={name: 0 for name in COUNTER_NAMES},
    )
