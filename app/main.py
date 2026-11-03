from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.arb_routes import router as arb_router
from app.api.lp_routes import router as lp_router
from app.api.mempool_routes import router as mempool_router
from app.api.routes import router
from app.api.testnet_routes import router as testnet_router
from app.config.settings import get_settings
from app.services.market_maker_loop import MarketMakerLoop
from app.services.mempool.watcher import MempoolWatcher
from app.services.testnet.trader import TestnetTrader

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    loop = MarketMakerLoop(settings)
    loop.initialize()
    app.state.market_maker_loop = loop
    # The mempool watcher runs beside the loop, never inside it (specs/004, D6).
    watcher = MempoolWatcher(settings, loop.repository) if settings.mempool_active else None
    app.state.mempool_watcher = watcher
    # The testnet trader also runs beside the loop; it needs free testnet keys (specs/005, D2).
    trader = (
        TestnetTrader(settings, loop.repository, loop.kill_switch)
        if settings.testnet_active
        else None
    )
    app.state.testnet_trader = trader
    if settings.loop_enabled:
        await loop.start()
    if watcher is not None:
        await watcher.start()
    if trader is not None:
        await trader.start()
    yield
    if trader is not None:
        await trader.stop()
    if watcher is not None:
        await watcher.stop()
    await loop.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="crypto-mm-lab", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(lp_router)
    app.include_router(arb_router)
    app.include_router(mempool_router)
    app.include_router(testnet_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=307)

    @app.get("/dashboard")
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

    return app


app = create_app()
