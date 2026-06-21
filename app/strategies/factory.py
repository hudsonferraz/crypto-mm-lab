from app.config.settings import Settings
from app.strategies.inventory_skew import InventorySkewStrategy
from app.strategies.pure_market_making import PureMarketMakingStrategy
from app.strategies.volatility_spread import VolatilitySpreadStrategy


def build_strategy(settings: Settings):
    if settings.strategy == "pure_mm":
        return PureMarketMakingStrategy(
            symbol=settings.symbol,
            spread_bps=settings.quote_spread_bps,
            quote_size=settings.quote_size,
        )
    if settings.strategy == "inventory_skew":
        return InventorySkewStrategy(
            symbol=settings.symbol,
            spread_bps=settings.quote_spread_bps,
            quote_size=settings.quote_size,
            target_base=settings.inventory_target_base,
            max_position_base=settings.max_position_base,
            skew_bps=settings.inventory_skew_bps,
        )
    if settings.strategy == "volatility_spread":
        return VolatilitySpreadStrategy(
            symbol=settings.symbol,
            spread_bps=settings.quote_spread_bps,
            quote_size=settings.quote_size,
            volatility_window=settings.volatility_window,
            volatility_spread_multiplier=settings.volatility_spread_multiplier,
        )
    raise ValueError(f"Unknown strategy: {settings.strategy}")
