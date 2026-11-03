"""Per-tick scan + simulate: outcomes, plain-language notes, status, cadence, accuracy."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.dex.executor_simulator import SimulatorUnavailable
from app.adapters.dex.v3_fixtures import load_quote_vectors
from app.services.flash_arb_service import (
    FIXTURE_REASON,
    FlashArbService,
    accuracy_summary,
    backend_label,
    disabled_status,
)
from tests.arb_helpers import EchoSimulator, StubReader, reserves_for

# Recorded mainnet state, treated as a live read for these tests.
SNAPSHOT = replace(load_quote_vectors().snapshot, data_source="live")
T0 = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


def _service(gap_bps: float, **kwargs) -> tuple[FlashArbService, StubReader, EchoSimulator]:
    reserves = reserves_for(SNAPSHOT, gap_bps=gap_bps)
    reader = StubReader(reserves)
    simulator = kwargs.pop("simulator", None) or EchoSimulator(SNAPSHOT, reserves)
    params = dict(
        backend="public-rpc",
        reference_size_quote=1_000.0,
        gas_units=160_000,
        priority_fee_gwei=0.05,
        fallback_gas_price_gwei=30.0,
        clock=lambda: T0,
    )
    params.update(kwargs)
    return FlashArbService(reserves_reader=reader, simulator=simulator, **params), reader, simulator


def test_wide_gap_is_profitable_and_matches_the_estimate_exactly() -> None:
    service, reader, simulator = _service(120.0)
    result = service.run(SNAPSHOT)
    assert result.outcome == "profitable"
    assert result.direction == "v3_to_v2" and result.size_reason == "optimal"
    assert result.sim_gross_profit_raw > 0
    assert result.estimate_error_quote == 0 and result.estimate_error_bps == 0
    assert result.sim_net_quote == pytest.approx(result.sim_gross_quote - result.gas_cost_quote)
    assert "cheaper on V3 than on V2" in result.outcome_note
    assert "only simulates" in result.outcome_note
    # Reserves and simulation are pinned to the snapshot's block.
    assert reader.calls == [SNAPSHOT.block_number]
    assert simulator.calls[0][2] == SNAPSHOT.block_number
    status = service.status
    assert status.state == "ok" and status.reason is None and status.last_success_at == T0


def test_gap_inside_fees_is_unprofitable_and_says_why() -> None:
    result = _service(10.0)[0].run(SNAPSHOT)
    assert result.outcome == "unprofitable"
    assert result.size_reason == "reference"
    assert result.sim_gross_quote < 0
    assert "smaller than both pools' fees (35 bps)" in result.outcome_note
    assert "before a block is published" in result.outcome_note


def test_gross_profit_eaten_by_gas_says_so() -> None:
    # Just past the fees: small positive gross; a huge base fee makes gas dominate.
    service, _, _ = _service(40.0)
    result = service.run(replace(SNAPSHOT, base_fee_wei=5_000 * 10**9))
    assert result.sim_gross_quote > 0 and result.sim_net_quote <= 0
    assert result.outcome == "unprofitable"
    assert "before gas, but gas costs" in result.outcome_note


def test_contract_revert_is_recorded_with_reason() -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=10.0)
    simulator = EchoSimulator(SNAPSHOT, reserves, error_name="InvalidAmount")
    result = _service(10.0, simulator=simulator)[0].run(SNAPSHOT)
    assert result.outcome == "reverted:InvalidAmount"
    assert result.sim_net_quote is None and result.gas_used is None
    assert "reverted (InvalidAmount)" in result.outcome_note


def test_estimate_error_is_reported_in_usdc_and_bps() -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=10.0)
    simulator = EchoSimulator(SNAPSHOT, reserves, profit_offset=-100_000)  # sim $0.10 worse
    result = _service(10.0, simulator=simulator)[0].run(SNAPSHOT)
    assert result.estimate_error_quote == pytest.approx(-0.1)
    assert result.estimate_error_bps == pytest.approx(-0.1 / result.notional_quote * 1e4)


@pytest.mark.parametrize(
    ("snapshot", "key", "text"),
    [
        (replace(SNAPSHOT, data_source="fixture"), "fixture", FIXTURE_REASON),
        (replace(SNAPSHOT, is_stale=True), "stale", "stale"),
    ],
)
def test_no_live_state_means_labeled_skip_not_fabrication(snapshot, key, text) -> None:
    service, reader, simulator = _service(120.0)
    assert service.run(snapshot) is None
    assert reader.calls == [] and simulator.calls == []
    assert service.status.state == "unavailable" and text in service.status.reason
    assert service.skipped == {key: 1}


def test_backend_failures_become_unavailable_with_a_reason() -> None:
    reserves = reserves_for(SNAPSHOT, gap_bps=10.0)
    simulator = EchoSimulator(
        SNAPSHOT, reserves, exception=SimulatorUnavailable("no state overrides")
    )
    service = _service(10.0, simulator=simulator)[0]
    assert service.run(SNAPSHOT) is None
    assert "can't run it: no state overrides" in service.status.reason
    simulator.exception = TimeoutError("slow")
    assert service.run(SNAPSHOT) is None
    assert "TimeoutError" in service.status.reason
    assert service.status.skipped_total == 2
    # Recovers on the next good tick.
    simulator.exception = None
    assert service.run(SNAPSHOT) is not None and service.status.state == "ok"


def test_reserve_read_failure_is_isolated() -> None:
    service, reader, _ = _service(10.0)
    reader.error = ConnectionError("rpc down")
    assert service.run(SNAPSHOT) is None
    assert service.status.state == "unavailable"


def _at_block(offset: int):
    return replace(SNAPSHOT, block_number=SNAPSHOT.block_number + offset)


def test_every_n_ticks_cadence() -> None:
    service, _, simulator = _service(10.0, every_ticks=3)
    results = [service.run(_at_block(i)) for i in range(7)]
    assert [r is not None for r in results] == [True, False, False, True, False, False, True]
    assert len(simulator.calls) == 3


def test_one_simulation_per_new_block() -> None:
    # Ticks (every few seconds) outpace blocks (12 s): the same state is simulated once.
    service, _, simulator = _service(10.0)
    assert service.run(SNAPSHOT) is not None
    assert service.run(SNAPSHOT) is None
    assert service.status.state == "ok" and service.status.skipped_total == 0
    assert service.run(_at_block(1)) is not None
    assert len(simulator.calls) == 2


def test_gas_price_falls_back_without_base_fee() -> None:
    result = _service(10.0)[0].run(replace(SNAPSHOT, base_fee_wei=None))
    assert result.gas_price_gwei == 30.0


def test_waiting_and_disabled_status() -> None:
    service = _service(10.0)[0]
    assert service.status.state == "waiting"
    status = disabled_status()
    assert not status.enabled and status.state == "disabled" and "ARB_SIM_ENABLED" in status.reason


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("https://ethereum.publicnode.com", "public-rpc"),
        ("http://127.0.0.1:8545", "local-fork"),
        ("https://eth-mainnet.g.alchemy.com/v2/KEY", "custom-rpc"),
        (None, "custom-rpc"),
    ],
)
def test_backend_label_never_exposes_the_url(url, label) -> None:
    assert backend_label(url) == label


def _results():
    rows = []
    for index, gap in enumerate((10.0, 120.0, 10.0, -120.0)):
        service = _service(gap, clock=lambda i=index: T0 + timedelta(seconds=i))[0]
        rows.append(service.run(SNAPSHOT))
    return rows


def test_accuracy_summary_counts_agreement_and_errors() -> None:
    rows = _results()
    summary = accuracy_summary(rows)
    assert summary.total == 4
    assert summary.outcome_counts == {"profitable": 2, "unprofitable": 2}
    assert summary.estimate_says_profitable == summary.simulation_says_profitable == 2
    assert summary.agreement_rate == 1.0
    assert summary.median_abs_error_bps == 0 and summary.p95_abs_error_bps == 0
    assert summary.exact_match_rate == 1.0
    assert summary.window_start == T0 and summary.window_end == T0 + timedelta(seconds=3)


def test_accuracy_summary_is_deterministic_and_handles_empty() -> None:
    rows = _results()
    assert accuracy_summary(rows) == accuracy_summary(list(reversed(rows)))
    empty = accuracy_summary([])
    assert empty.total == 0 and empty.agreement_rate is None
