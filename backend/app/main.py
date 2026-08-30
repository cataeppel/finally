# backend/app/main.py
"""FastAPI application entrypoint.

Only the market data module (PLAN.md §6, MARKET_DATA.md) is implemented so
far. Portfolio, watchlist and chat routers are out of scope for this build and are
added by other agents on top of `MarketDataService` — see the module boundary in
MARKET_DATA.md §1.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .market import MarketDataService, build_source
from .routes.market import router as market_router

#: The ten default watchlist tickers (PLAN.md §7 seed data). Until the watchlist/
#: portfolio backend lands there is no database to compute `watchlist ∪ held` from
#: (MARKET_DATA.md §7's tracked ticker set), so the tracked set is pinned
#: to the defaults for this build. Wire `refresh_tracked()` here once that module
#: exists — this constant is exactly the placeholder it should replace.
DEFAULT_WATCHLIST = frozenset(
    {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    market_service = MarketDataService(build_source())
    app.state.market = market_service
    await market_service.start()
    market_service.set_tracked(DEFAULT_WATCHLIST)
    try:
        yield
    finally:
        await market_service.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)

# ---- ROUTE ORDER MATTERS (PLAN.md §11) --------------------------------
# every /api/* router first; the static frontend export (once it exists) is
# mounted last so a catch-all at "/" never shadows an API route.
app.include_router(market_router)
