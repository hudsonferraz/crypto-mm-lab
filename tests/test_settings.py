import pytest
from pydantic import ValidationError

from app.config.settings import Settings, get_settings


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("QUOTE_SPREAD_BPS", raising=False)
    monkeypatch.delenv("MAKER_FEE_BPS", raising=False)
    monkeypatch.delenv("TAKER_FEE_BPS", raising=False)
    monkeypatch.delenv("FILL_MODE", raising=False)
    monkeypatch.delenv("MARKET_DATA_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.exchange == "binance"
    assert settings.symbol == "BTC/USDT"
    assert settings.poll_interval_sec == 2.0
    assert settings.quote_spread_bps == 30.0
    assert settings.maker_fee_bps == 2.0
    assert settings.quote_size == 0.001
    assert settings.max_position_base == 0.01
    assert settings.db_url == "sqlite:///./data/mm_lab.db"
    assert settings.strategy == "pure_mm"
    assert settings.fill_mode == "full_cross_fill"
    assert settings.market_data_mode == "poll"
    assert settings.quote_spread_bps > settings.maker_fee_bps * 2


def test_explicit_db_url_overrides_default() -> None:
    db_url = "sqlite:///test.db"

    settings = Settings(db_url=db_url)

    assert settings.db_url == db_url


def test_settings_reject_zero_report_interval_ticks() -> None:
    with pytest.raises(ValidationError):
        Settings(report_interval_ticks=0)


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
