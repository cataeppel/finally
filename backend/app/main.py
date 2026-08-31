"""FastAPI application for FinAlly (PLAN.md §3)."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.market import PriceCache, create_market_data_source, create_stream_router
from app.routes import chat, portfolio, watchlist
from app.routes.trading import record_snapshot, tickers_to_track

logger = logging.getLogger(__name__)

#: PLAN.md §7 — portfolio value is snapshotted on this cadence.
SNAPSHOT_INTERVAL = 30

# Module-level PriceCache — shared between the SSE router and the rest of the app
price_cache = PriceCache()


async def _snapshot_loop(cache: PriceCache, interval: float = SNAPSHOT_INTERVAL):
    """Background task: record a portfolio snapshot every 30 seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            await record_snapshot(cache)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error recording portfolio snapshot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    await init_db()

    source = create_market_data_source(price_cache)

    app.state.price_cache = price_cache
    app.state.market_source = source

    tickers = await tickers_to_track()
    await source.start(tickers)
    logger.info("Market data source started with %d tickers", len(tickers))

    # Opening snapshot so the P&L chart has a point from the first moment.
    await record_snapshot(price_cache)

    snapshot_task = asyncio.create_task(_snapshot_loop(price_cache))

    yield

    snapshot_task.cancel()
    try:
        await snapshot_task
    except asyncio.CancelledError:
        pass

    await source.stop()
    logger.info("Market data source stopped")


app = FastAPI(title="FinAlly", lifespan=lifespan)

# API routes
app.include_router(portfolio.router)
app.include_router(watchlist.router)
app.include_router(chat.router)

# SSE streaming — uses the module-level price_cache
app.include_router(create_stream_router(price_cache))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Static frontend (Next.js export) — mounted last so /api/* routes take priority
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
else:  # pragma: no cover - dev convenience when the frontend hasn't been built
    logger.warning("No static/ directory at %s; serving API only", _static_dir)
