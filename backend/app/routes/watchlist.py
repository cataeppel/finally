"""Watchlist API endpoints (PLAN.md §8)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import (
    DuplicateTickerError,
    add_to_watchlist,
    get_watchlist,
    remove_from_watchlist,
)

from .trading import TradeError, normalize_ticker, sync_tracked_tickers

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("")
async def list_watchlist(request: Request):
    """Current watchlist tickers with their latest prices from the PriceCache."""
    cache = request.app.state.price_cache
    watchlist = await get_watchlist()

    items = []
    for entry in watchlist:
        ticker = entry["ticker"]
        update = cache.get(ticker)
        items.append({
            "ticker": ticker,
            "price": update.price if update else None,
            "previous_price": update.previous_price if update else None,
            "change": update.change if update else None,
            "change_percent": update.change_percent if update else None,
            "direction": update.direction if update else None,
        })

    return {"watchlist": items}


@router.post("")
async def add_ticker(body: AddTickerRequest, request: Request):
    """Add a ticker to the watchlist and start streaming prices for it."""
    try:
        ticker = normalize_ticker(body.ticker)
    except TradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The repository raises on the UNIQUE(user_id, ticker) violation, so a
    # genuine DB failure surfaces as a 500 rather than a bogus 409.
    try:
        entry = await add_to_watchlist(ticker)
    except DuplicateTickerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Seeds a price for the ticker so it can be traded immediately.
    await sync_tracked_tickers(request.app.state.market_source, request.app.state.price_cache)

    return {"ticker": ticker, "added_at": entry["added_at"]}


@router.delete("/{ticker}")
async def remove_ticker(ticker: str, request: Request):
    """Remove a ticker from the watchlist.

    A ticker with an open position keeps streaming: the positions table, the
    heatmap and any sell order all need a live price for it.
    """
    ticker = ticker.strip().upper()

    if not await remove_from_watchlist(ticker):
        raise HTTPException(status_code=404, detail=f"{ticker} not in watchlist")

    await sync_tracked_tickers(request.app.state.market_source, request.app.state.price_cache)

    return {"ticker": ticker, "removed": True}
