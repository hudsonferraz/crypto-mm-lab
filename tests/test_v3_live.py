"""Opt-in live checks against the free public RPC: ``pytest --live tests/test_v3_live.py``."""

import pytest

pytestmark = pytest.mark.live


PUBLIC_RPC = "https://ethereum.publicnode.com"


def test_live_simulator_matches_quoter(monkeypatch) -> None:
    from app.config.settings import get_settings
    from scripts.verify_v3_quotes import main

    # conftest points ETH_RPC_URL at a dead port to keep the default suite offline
    monkeypatch.setenv("ETH_RPC_URL", PUBLIC_RPC)
    get_settings.cache_clear()
    assert main(["--samples", "21"]) == 0


def test_live_adapter_reads_block_consistent_snapshot() -> None:
    from app.adapters.dex.multicall import Web3Transport
    from app.adapters.dex.v3_pool_adapter import V3PoolAdapter

    adapter = V3PoolAdapter(
        Web3Transport(PUBLIC_RPC, timeout_sec=20),
        "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
    )
    snapshot = adapter.read_snapshot()
    assert snapshot.window_is_current
    assert 100 < snapshot.price < 100_000
    assert snapshot.metadata.fee_pips == 500
