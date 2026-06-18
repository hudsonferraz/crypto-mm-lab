from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config.settings import get_settings
from app.services.market_maker_loop import MarketMakerLoop

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    loop = MarketMakerLoop(settings)
    loop.initialize()
    app.state.market_maker_loop = loop
    await loop.start()
    yield
    await loop.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="crypto-mm-lab", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/dashboard")
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

    return app


app = create_app()
