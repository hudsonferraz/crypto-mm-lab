from app.config.settings import Settings, get_settings


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.exchange == "binance"
    assert settings.symbol == "BTC/USDT"
    assert settings.poll_interval_sec == 2.0
    assert settings.quote_spread_bps == 10.0
    assert settings.quote_size == 0.001
    assert settings.max_position_base == 0.01
    assert settings.db_url == "sqlite:///./data/mm_lab.db"
    assert settings.strategy == "pure_mm"
    assert settings.fill_mode == "full_cross_fill"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
