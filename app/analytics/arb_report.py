"""Serialization of flash-arb simulations for storage and the API (contracts/api.md)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from app.models.flash_arb import AccuracySummary, SimulationResult, SimulatorStatus

_RAW_INT_FIELDS = (
    "amount_in",
    "sim_v3_amount_in",
    "sim_v3_amount_out",
    "sim_v2_amount_in",
    "sim_v2_amount_out",
    "sim_gross_profit_raw",
)


def simulation_to_dict(result: SimulationResult) -> dict:
    """Flat storage form; raw ints become strings (they can exceed 2^63)."""
    data = asdict(result)
    for name in _RAW_INT_FIELDS:
        if data[name] is not None:
            data[name] = str(data[name])
    data["timestamp"] = result.timestamp.isoformat()
    return data


def simulation_from_dict(data: dict) -> SimulationResult:
    values = dict(data)
    for name in _RAW_INT_FIELDS:
        if values.get(name) is not None:
            values[name] = int(values[name])
    values["timestamp"] = datetime.fromisoformat(values["timestamp"])
    return SimulationResult(**values)


def _raw(value: int | None) -> str | None:
    return None if value is None else str(value)


def simulation_payload(result: SimulationResult) -> dict:
    """API shape: estimate next to simulation, with the explanation (FR-028)."""
    return {
        "tick_id": result.tick_id,
        "block_number": result.block_number,
        "direction": result.direction,
        "amount_in_raw": str(result.amount_in),
        "size_reason": result.size_reason,
        "notional_quote": result.notional_quote,
        "gap_bps": result.gap_bps,
        "estimate": {
            "gross_quote": result.estimate_gross_quote,
            "net_quote": result.estimate_net_quote,
            "exact": result.exact_estimate,
        },
        "simulation": {
            "gross_quote": result.sim_gross_quote,
            "gas_used": result.gas_used,
            "gas_price_gwei": result.gas_price_gwei,
            "gas_cost_quote": result.gas_cost_quote,
            "net_quote": result.sim_net_quote,
            "v3_amount_in_raw": _raw(result.sim_v3_amount_in),
            "v3_amount_out_raw": _raw(result.sim_v3_amount_out),
            "v2_amount_in_raw": _raw(result.sim_v2_amount_in),
            "v2_amount_out_raw": _raw(result.sim_v2_amount_out),
        },
        "outcome": result.outcome,
        "outcome_note": result.outcome_note,
        "estimate_error_quote": result.estimate_error_quote,
        "estimate_error_bps": result.estimate_error_bps,
        "backend": result.backend,
        "latency_ms": result.latency_ms,
        "timestamp": result.timestamp.isoformat(),
    }


def status_payload(status: SimulatorStatus) -> dict:
    return {
        "enabled": status.enabled,
        "state": status.state,
        "reason": status.reason,
        "backend": status.backend,
        "skipped_total": status.skipped_total,
        "last_success_at": status.last_success_at.isoformat() if status.last_success_at else None,
    }


def accuracy_payload(summary: AccuracySummary) -> dict:
    data = asdict(summary)
    for name in ("window_start", "window_end"):
        data[name] = data[name].isoformat() if data[name] else None
    return data
