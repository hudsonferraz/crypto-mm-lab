import asyncio
import threading

import pytest

from app.config.settings import Settings
from app.simlab.recording import BundleLibrary, bundle_info_for, save_recording, write_bundle_index
from app.simlab.service import LabRefused, SimLab, SimulationRequest, normalize_visitor_id
from tests.simlab_helpers import fake_clients, make_recording

A = "a" * 32
B = "b" * 32
WAVE = [80_000.0, 80_010.0, 80_020.0, 80_010.0, 80_000.0, 79_990.0, 79_980.0, 79_990.0] * 3


def bundled(**overrides) -> SimulationRequest:
    values = {
        "source": "bundled",
        "bundle_id": "test-btc",
        "strategy": "pure_mm",
        "spread_bps": 2.0,
        "fill_model": "full_cross_fill",
    }
    return SimulationRequest(**{**values, **overrides})


def live(**overrides) -> SimulationRequest:
    values = {
        "source": "live",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "strategy": "pure_mm",
        "spread_bps": 2.0,
        "fill_model": "trade_fill",
        "duration_sec": 30,
    }
    return SimulationRequest(**{**values, **overrides})


@pytest.fixture
def library(tmp_path) -> BundleLibrary:
    recording = make_recording(WAVE, [(1.5, 79_000.0, 0.5)])
    save_recording(recording, tmp_path / "test-btc.json.gz")
    write_bundle_index(tmp_path, [bundle_info_for("test-btc", recording, "test-btc.json.gz")])
    return BundleLibrary(tmp_path)


class GatedRecorder:
    """Records instantly, but only after the test opens the gate."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.gate = threading.Event()
        self.calls: list[str] = []
        self.fail = fail

    def __call__(self, clients, exchange, symbol, rules, duration, *, on_progress, should_stop):
        self.calls.append(symbol)
        on_progress(duration / 2)
        self.gate.wait(5)
        if self.fail is not None:
            raise self.fail
        return make_recording(WAVE, [(1.5, 79_000.0, 0.5)])


def make_lab(library, *, recorder=None, clock=None, **settings) -> SimLab:
    return SimLab(
        Settings(**settings),
        clients=fake_clients(),
        library=library,
        recorder=recorder or GatedRecorder(),
        clock=clock or (lambda: 1_000.0),
    )


async def wait_for(lab: SimLab, visitor: str, run_id: str, stage: str) -> dict:
    for _ in range(200):
        run = lab.get_run(visitor, run_id)
        if run and run["stage"] == stage:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"run never reached {stage}: {lab.get_run(visitor, run_id)}")


def test_visitor_ids_are_32_hex_digits() -> None:
    assert normalize_visitor_id("12345678-1234-1234-1234-1234567890AB") == (
        "123456781234123412341234567890ab"
    )
    assert normalize_visitor_id("not-an-id") is None
    assert normalize_visitor_id(None) is None


async def test_bundled_run_finishes_at_once_and_is_cached(library) -> None:
    lab = make_lab(library)
    try:
        run = await lab.submit(A, "1.1.1.1", bundled())
        done = lab.get_run(A, run.run_id)
        assert done["stage"] == "done"
        result = done["result"]
        assert result["recording"]["symbol"] == "BTC/USDT"
        assert result["summary"]
        again = await lab.submit(B, "2.2.2.2", bundled())
        assert lab.get_run(B, again.run_id)["result"] == result  # FR-008
    finally:
        await lab.stop()


async def test_live_run_goes_through_the_stages(library) -> None:
    recorder = GatedRecorder()
    lab = make_lab(library, recorder=recorder)
    try:
        run = await lab.submit(A, "1.1.1.1", live())
        await wait_for(lab, A, run.run_id, "recording")
        for _ in range(100):  # the progress callback runs in the worker thread
            if lab.get_run(A, run.run_id)["seconds_left"] == 15:
                break
            await asyncio.sleep(0.01)
        assert lab.get_run(A, run.run_id)["seconds_left"] == 15
        recorder.gate.set()
        done = await wait_for(lab, A, run.run_id, "done")
        assert done["result"]["fill_count"] >= 1  # the trade at 79,000 fills the bid
        assert recorder.calls == ["BTC/USDT"]
    finally:
        await lab.stop()


async def test_a_failed_recording_says_why(library) -> None:
    recorder = GatedRecorder(fail=RuntimeError("boom at https://secret.invalid/x"))
    recorder.gate.set()
    lab = make_lab(library, recorder=recorder)
    try:
        run = await lab.submit(A, "1.1.1.1", live())
        failed = await wait_for(lab, A, run.run_id, "failed")
        assert "boom" in failed["error"] and "https://" not in failed["error"]
    finally:
        await lab.stop()


async def test_visitors_see_only_their_own_runs(library) -> None:
    lab = make_lab(library)
    try:
        run_a = await lab.submit(A, "1.1.1.1", bundled())
        run_b = await lab.submit(B, "2.2.2.2", bundled(spread_bps=5.0))
        assert [run["run_id"] for run in lab.list_runs(A)] == [run_a.run_id]
        assert [run["run_id"] for run in lab.list_runs(B)] == [run_b.run_id]
        assert lab.get_run(B, run_a.run_id) is None
        assert "result" not in lab.list_runs(A)[0] and "headline" in lab.list_runs(A)[0]
    finally:
        await lab.stop()


async def test_one_active_run_per_visitor(library) -> None:
    recorder = GatedRecorder()
    lab = make_lab(library, recorder=recorder)
    try:
        first = await lab.submit(A, "1.1.1.1", live())
        with pytest.raises(LabRefused) as refused:
            await lab.submit(A, "1.1.1.1", bundled())
        assert refused.value.status == 409 and refused.value.run_id == first.run_id
        recorder.gate.set()
        await wait_for(lab, A, first.run_id, "done")
        await lab.submit(A, "1.1.1.1", bundled())
    finally:
        recorder.gate.set()
        await lab.stop()


async def test_hourly_limits_per_visitor_and_per_address(library) -> None:
    now = [1_000.0]
    recorder = GatedRecorder()
    recorder.gate.set()
    lab = make_lab(
        library,
        recorder=recorder,
        clock=lambda: now[0],
        lab_runs_per_hour=2,
        lab_bundled_runs_per_hour=3,
    )
    try:
        for spread in (2.0, 3.0, 4.0):
            await lab.submit(A, "1.1.1.1", bundled(spread_bps=spread))
        with pytest.raises(LabRefused) as refused:
            await lab.submit(A, "1.1.1.1", bundled(spread_bps=6.0))
        assert refused.value.status == 429 and refused.value.limit == "per_hour"
        assert refused.value.retry_after_sec == pytest.approx(3600)
        # A new visitor id on the same address does not lift the limit.
        with pytest.raises(LabRefused, match="network address"):
            await lab.submit(B, "1.1.1.1", bundled(spread_bps=7.0))
        # Live recordings have their own budget.
        for _ in range(2):
            run = await lab.submit(A, "1.1.1.1", live())
            await wait_for(lab, A, run.run_id, "done")
        with pytest.raises(LabRefused, match="live recordings"):
            await lab.submit(A, "1.1.1.1", live())
        now[0] += 3601
        await lab.submit(A, "1.1.1.1", bundled(spread_bps=8.0))
    finally:
        await lab.stop()


async def test_full_queue_refuses_with_retry_time(library) -> None:
    recorder = GatedRecorder()
    lab = make_lab(library, recorder=recorder, lab_concurrent_recordings=1, lab_queue_size=1)
    try:
        await lab.submit("1" * 32, "1.1.1.1", live())
        await asyncio.sleep(0.05)  # the worker takes the first run
        waiting = await lab.submit("2" * 32, "2.2.2.2", live())
        assert lab.get_run("2" * 32, waiting.run_id)["queue_position"] == 1
        with pytest.raises(LabRefused) as refused:
            await lab.submit("3" * 32, "3.3.3.3", live())
        assert refused.value.limit == "queue_full" and refused.value.retry_after_sec
    finally:
        recorder.gate.set()
        await lab.stop()


async def test_runs_expire_and_only_the_newest_are_kept(library) -> None:
    now = [1_000.0]
    lab = make_lab(library, clock=lambda: now[0], lab_runs_kept=2, lab_keep_hours=1)
    try:
        ids = []
        for spread in (2.0, 3.0, 4.0):
            now[0] += 1
            ids.append((await lab.submit(A, "1.1.1.1", bundled(spread_bps=spread))).run_id)
        assert [run["run_id"] for run in lab.list_runs(A)] == ids[:0:-1]
        now[0] += 3601
        assert lab.list_runs(A) == []
    finally:
        await lab.stop()


async def test_invalid_requests_are_refused_before_anything_runs(library) -> None:
    recorder = GatedRecorder()
    lab = make_lab(library, recorder=recorder)
    try:
        cases = [
            live(symbol="PEPE/USDT"),
            live(exchange="ftx"),
            live(duration_sec=10),
            live(duration_sec=None),
            live(strategy="martingale"),
            live(fill_model="magic"),
            bundled(spread_bps=500.0),
            bundled(bundle_id="missing"),
            SimulationRequest("other", "pure_mm", 2.0, "full_cross_fill"),  # type: ignore[arg-type]
        ]
        for request in cases:
            with pytest.raises(LabRefused) as refused:
                await lab.submit(A, "1.1.1.1", request)
            assert refused.value.status == 422, request
        assert recorder.calls == []
        assert lab.list_runs(A) == []
    finally:
        await lab.stop()


async def test_options_list_exchanges_bundles_and_limits(library) -> None:
    lab = make_lab(library)
    try:
        options = await lab.options()
        assert [exchange["id"] for exchange in options["exchanges"]] == [
            "binance",
            "kraken",
            "coinbase",
        ]
        binance = options["exchanges"][0]
        assert [pair["symbol"] for pair in binance["pairs"]] == ["BTC/USDT", "ETH/USDT"]
        assert options["bundles"][0]["id"] == "test-btc"
        assert "file" not in options["bundles"][0]
        assert options["limits"]["concurrent_recordings"] == 2
        assert {d["id"] for d in options["decisions"]} == {f"D{i}" for i in range(1, 7)}
        assert any(model["id"] == "trade_fill" for model in options["fill_models"])
    finally:
        await lab.stop()
