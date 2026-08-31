"""Tests for the SSE price stream (PLAN.md §6)."""

import json

import pytest
from fastapi import FastAPI

from app.market import PriceCache, create_stream_router
from app.market.stream import _generate_events


class StubRequest:
    """Minimal stand-in for fastapi.Request that disconnects after N polls."""

    def __init__(self, disconnect_after: int = 1):
        self.client = None
        self._polls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._polls += 1
        return self._polls > self._disconnect_after


async def collect(gen) -> list[str]:
    return [chunk async for chunk in gen]


def parse_data(chunk: str) -> dict:
    assert chunk.startswith("data: ") and chunk.endswith("\n\n")
    return json.loads(chunk[len("data: ") : -2])


class TestGenerateEvents:
    async def test_first_chunk_is_retry_directive(self):
        cache = PriceCache()
        chunks = await collect(_generate_events(cache, StubRequest(0), interval=0))
        assert chunks[0] == "retry: 1000\n\n"

    async def test_event_shape_matches_price_update(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("AAPL", 191.0)

        chunks = await collect(_generate_events(cache, StubRequest(1), interval=0))
        payload = parse_data(chunks[1])

        assert set(payload) == {"AAPL"}
        aapl = payload["AAPL"]
        assert aapl["ticker"] == "AAPL"
        assert aapl["price"] == 191.0
        assert aapl["previous_price"] == 190.0
        assert aapl["direction"] == "up"
        assert aapl["change"] == pytest.approx(1.0)
        assert "timestamp" in aapl and "change_percent" in aapl

    async def test_streams_every_tracked_ticker(self):
        cache = PriceCache()
        for ticker, price in (("AAPL", 190.0), ("GOOGL", 175.0), ("MSFT", 420.0)):
            cache.update(ticker, price)

        chunks = await collect(_generate_events(cache, StubRequest(1), interval=0))
        assert set(parse_data(chunks[1])) == {"AAPL", "GOOGL", "MSFT"}

    async def test_unchanged_cache_emits_no_duplicate_data(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        # Poll several times without touching the cache.
        chunks = await collect(
            _generate_events(cache, StubRequest(4), interval=0, heartbeat=1e6)
        )
        data_chunks = [c for c in chunks if c.startswith("data: ")]
        assert len(data_chunks) == 1

    async def test_heartbeat_when_idle(self):
        cache = PriceCache()
        chunks = await collect(
            _generate_events(cache, StubRequest(2), interval=0, heartbeat=0)
        )
        assert [c for c in chunks if c.startswith(":")]

    async def test_stops_on_client_disconnect(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        request = StubRequest(1)

        chunks = await collect(_generate_events(cache, request, interval=0))

        # Generator returned rather than looping forever.
        assert chunks[0] == "retry: 1000\n\n"
        assert request._polls == 2


class TestStreamRoute:
    def test_factory_returns_independent_routers(self):
        cache = PriceCache()
        first = create_stream_router(cache)
        second = create_stream_router(cache)

        assert first is not second
        assert len(first.routes) == 1
        assert len(second.routes) == 1
        assert first.routes[0].path == "/api/stream/prices"

    async def test_endpoint_returns_event_stream_response(self):
        """Call the route handler directly: the generator never ends on its own,
        so consume a bounded number of chunks rather than draining the body."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        app = FastAPI()
        app.include_router(create_stream_router(cache))
        handler = app.routes[-1].endpoint

        request = StubRequest(1)
        response = await handler(request)

        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

        chunks = await collect(response.body_iterator)
        assert chunks[0] == "retry: 1000\n\n"
        assert parse_data(chunks[1])["AAPL"]["price"] == 190.0
