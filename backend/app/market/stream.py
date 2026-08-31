"""SSE streaming endpoint for live price updates (PLAN.md §6)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

#: Push cadence — matches the simulator's ~500ms update interval.
STREAM_INTERVAL = 0.5

#: Send a comment line if nothing has changed for this long, so idle
#: connections stay open through proxies and disconnects are noticed promptly.
HEARTBEAT_INTERVAL = 15.0


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Build the SSE router bound to a specific PriceCache.

    A fresh APIRouter is created per call so the factory can be used more than
    once (tests build their own app) without duplicate route registration.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        The client connects with `EventSource` and receives events shaped as a
        map of every tracked ticker to its latest update:

            data: {"AAPL": {"ticker": "AAPL", "price": 190.5, "previous_price":
                   190.4, "timestamp": 1.7e9, "change": 0.1,
                   "change_percent": 0.05, "direction": "up"}, ...}

        A `retry` directive is sent first so the browser auto-reconnects.
        """
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = STREAM_INTERVAL,
    heartbeat: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted price events until the client disconnects.

    Emits a full price snapshot whenever the cache version changes (at most
    once per `interval`), plus a comment heartbeat during quiet periods.
    """
    # Tell the client to retry after 1 second if the connection drops
    yield "retry: 1000\n\n"

    last_version = -1
    last_emit = 0.0
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            now = time.monotonic()
            current_version = price_cache.version
            prices = price_cache.get_all() if current_version != last_version else {}

            if prices:
                last_version = current_version
                last_emit = now
                data = {ticker: update.to_dict() for ticker, update in prices.items()}
                yield f"data: {json.dumps(data)}\n\n"
            elif now - last_emit >= heartbeat:
                last_emit = now
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
        raise
