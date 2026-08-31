"""Tests for app wiring in app/main.py (PLAN.md §3, §7, §8)."""

import asyncio

from app.db import add_to_watchlist, get_portfolio_history, upsert_position
from app.main import SNAPSHOT_INTERVAL, _snapshot_loop, app
from app.market import PriceCache
from app.routes.trading import tickers_to_track


class TestRouteRegistration:
    def test_every_planned_endpoint_is_registered(self):
        paths = {(route.path, tuple(sorted(route.methods))) for route in app.routes
                 if hasattr(route, "methods")}
        assert ("/api/health", ("GET",)) in paths
        assert ("/api/portfolio", ("GET",)) in paths
        assert ("/api/portfolio/trade", ("POST",)) in paths
        assert ("/api/portfolio/history", ("GET",)) in paths
        assert ("/api/watchlist", ("GET",)) in paths
        assert ("/api/watchlist", ("POST",)) in paths
        assert ("/api/watchlist/{ticker}", ("DELETE",)) in paths
        assert ("/api/chat", ("POST",)) in paths
        assert ("/api/stream/prices", ("GET",)) in paths


class TestTickersToTrack:
    async def test_defaults_to_the_seeded_watchlist(self, test_db):
        tickers = await tickers_to_track()
        assert len(tickers) == 10
        assert "AAPL" in tickers

    async def test_includes_held_tickers_off_the_watchlist(self, test_db):
        await upsert_position("PLTR", 3, 20.0)
        assert "PLTR" in await tickers_to_track()

    async def test_does_not_duplicate_held_watchlist_tickers(self, test_db):
        await upsert_position("AAPL", 3, 190.0)
        tickers = await tickers_to_track()
        assert tickers.count("AAPL") == 1

    async def test_added_tickers_are_tracked(self, test_db):
        await add_to_watchlist("PYPL")
        assert "PYPL" in await tickers_to_track()


class TestSnapshotLoop:
    def test_interval_matches_the_spec(self):
        assert SNAPSHOT_INTERVAL == 30

    async def test_records_a_snapshot_each_tick(self, test_db):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        await upsert_position("AAPL", 2, 180.0)

        task = asyncio.create_task(_snapshot_loop(cache, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        history = await get_portfolio_history()
        assert len(history) >= 2
        assert history[0]["total_value"] == 10381.0  # 10000 cash + 2 * 190.50

    async def test_survives_a_failing_snapshot(self, test_db, monkeypatch):
        """A transient DB error must not kill the background task."""
        calls = {"n": 0}

        async def flaky(_cache):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")

        monkeypatch.setattr("app.main.record_snapshot", flaky)

        task = asyncio.create_task(_snapshot_loop(PriceCache(), interval=0.01))
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert calls["n"] >= 2
