"""MARKET_DATA.md §10 -- the market half of the API surface."""
from __future__ import annotations

import re

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.market.service import MarketDataService
from app.market.simulator import SimulatedSource
from app.routes import market as market_routes

from .conftest import wait_for

ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def _make_app(svc: MarketDataService) -> FastAPI:
    app = FastAPI()
    app.state.market = svc
    app.include_router(market_routes.router)
    return app


@pytest.fixture
async def svc():
    service = MarketDataService(SimulatedSource(seed=1, interval=0.02), broadcast_interval=0.02)
    await service.start()
    service.set_tracked(frozenset({"AAPL"}))
    yield service
    await service.stop()


@pytest.fixture
async def client(svc):
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_history_returns_iso_z_millisecond_timestamps(client, svc):
    await wait_for(lambda: len(svc.history("AAPL")) > 0, timeout=5)
    resp = await client.get("/api/history/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["points"]
    for p in body["points"]:
        assert ISO_Z.match(p["ts"]), p["ts"]


async def test_history_of_an_untracked_ticker_is_200_with_no_points(client):
    resp = await client.get("/api/history/ZZZZ")
    assert resp.status_code == 200
    assert resp.json() == {"ticker": "ZZZZ", "points": []}


async def test_history_lowercases_are_normalised_to_upper(client, svc):
    await wait_for(lambda: len(svc.history("AAPL")) > 0, timeout=5)
    resp = await client.get("/api/history/aapl")
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"


def _scope_request() -> Request:
    """The minimal Request the route needs. `stream_prices` never reads it -- Starlette
    injects it, and the disconnect it represents arrives as generator cancellation."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/stream/prices",
            "headers": [],
            "query_string": b"",
        }
    )


async def _frames(svc: MarketDataService, *, limit: int) -> list[str]:
    """Read `limit` SSE frames straight off the route's async generator.

    NOT through httpx: `httpx.ASGITransport` awaits the ASGI app to completion and
    buffers the whole body before returning a response, so an endless SSE endpoint
    hangs it forever. Starlette pulls `StreamingResponse.body_iterator` one chunk at a
    time, which is exactly what this does -- the same code path the server runs, minus
    the socket. Closing the generator is what a client disconnect does.
    """
    response = await market_routes.stream_prices(_scope_request(), svc)
    agen = response.body_iterator
    out: list[str] = []
    try:
        async for chunk in agen:
            out.append(chunk if isinstance(chunk, str) else chunk.decode())
            if len(out) >= limit:
                break
    finally:
        await agen.aclose()
    return out


async def test_sse_emits_a_status_frame_first_then_ticks(svc):
    await wait_for(lambda: svc.price("AAPL") is not None, timeout=5)
    frames = await _frames(svc, limit=3)
    assert frames[0].startswith("event: status\ndata: ")
    ticks = [f for f in frames[1:] if f.startswith("data: ")]
    assert ticks, frames
    assert '"ticker": "AAPL"' in ticks[0]


async def test_sse_sends_a_heartbeat_when_idle(monkeypatch):
    """A dedicated service with NO tracked tickers -- the `svc` fixture tracks AAPL and
    would keep the subscriber's queue busy, so it would never idle down to the
    heartbeat path within a reasonable test timeout."""
    monkeypatch.setattr(market_routes, "HEARTBEAT_SECONDS", 0.05)
    idle_service = MarketDataService(SimulatedSource(seed=1, interval=0.02))
    await idle_service.start()
    try:
        frames = await _frames(idle_service, limit=2)
    finally:
        await idle_service.stop()
    assert frames[0].startswith("event: status")
    assert frames[1] == ": ping\n\n"


async def test_sse_unsubscribes_on_client_disconnect(svc):
    await _frames(svc, limit=1)          # the generator is closed on the way out
    await wait_for(lambda: svc.health["subscribers"] == 0, timeout=5)


async def test_health_reports_degraded_market_as_a_healthy_container():
    """Build the service around a source whose `degraded_reason` is set, rather
    than reaching into MarketDataService's private state."""
    from app.market.source import MarketDataSource
    from app.market.types import Quote, utcnow

    class EodSource(MarketDataSource):
        name = "eod"
        poll_interval = 60.0
        degraded_reason = "end-of-day data (free Massive tier)"

        def set_tickers(self, tickers):
            pass

        async def fetch(self):
            return [Quote("AAPL", 1.0, utcnow())]

    service = MarketDataService(EodSource())
    await service.start()
    app = _make_app(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"                # outer status: container is healthy
    assert body["market"]["status"] == "degraded"
    await service.stop()
