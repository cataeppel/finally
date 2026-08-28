# backend/app/routes/market.py
"""The market half of the API: SSE price stream, history, health.

PLAN.md §8. Owned by the market data module per MARKET_DATA_DESIGN.md §1 — the
database, trades, positions, watchlist and LLM chat are out of scope here and are
built by other agents on top of MarketDataService.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import market
from ..market import MarketDataService

router = APIRouter(prefix="/api")

HEARTBEAT_SECONDS = 15.0


def _sse(data: str, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


@router.get("/stream/prices")
async def stream_prices(request: Request, svc: MarketDataService = Depends(market)):
    """Long-lived SSE stream. PLAN.md §6.

    Wire contract:
      * price ticks are sent as the DEFAULT (unnamed) event, so the browser's
        `EventSource.onmessage` receives them with no extra wiring;
      * stream health is sent as a named `status` event, on connect and on change;
      * `: ping` comments every 15 s keep idle connections alive through proxies.
    """
    async def gen():
        q = svc.subscribe()
        last_status = None
        try:
            while True:
                if svc.status != last_status:
                    last_status = svc.status
                    yield _sse(json.dumps(svc.health), event="status")
                try:
                    batch = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"      # a comment: EventSource ignores it
                    continue
                for tick in batch:
                    yield _sse(json.dumps(tick.to_payload()))
        finally:
            svc.unsubscribe(q)              # runs on client disconnect (task cancelled)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",      # stops proxy buffering from batching frames
        },
    )


@router.get("/history/{ticker}")
async def history(ticker: str, svc: MarketDataService = Depends(market)):
    """Ring-buffer history for seeding charts and sparklines. PLAN.md §8.

    Always 200, even for an untracked ticker: an empty `points` array is the honest
    answer and lets the frontend render a placeholder rather than an error toast.
    """
    symbol = ticker.strip().upper()
    return {
        "ticker": symbol,
        "points": [p.to_payload() for p in svc.history(symbol)],
    }


@router.get("/health")
async def health(svc: MarketDataService = Depends(market)):
    return {"status": "ok", "market": svc.health}
