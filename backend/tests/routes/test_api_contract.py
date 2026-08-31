"""Response-shape contract for the API the frontend consumes (PLAN.md §8).

The frontend maps these fields directly (frontend/lib/api.ts, lib/types.ts) and
the E2E suite asserts on strings built from them, so a rename or a unit change
here breaks the UI silently. These tests pin the field names rather than the
values — behaviour is covered in test_portfolio.py / test_trading.py.
"""

import json
import time

import pytest

from app.db import upsert_position
from app.market import PriceCache
from app.market.stream import _generate_events

from ..market.test_stream import StubRequest


class TestPortfolioContract:
    async def test_top_level_fields(self, client):
        body = (await client.get("/api/portfolio")).json()
        assert set(body) == {
            "positions",
            "cash",
            "total_market_value",
            "total_value",
            "unrealized_pnl",
        }

    async def test_position_fields(self, client, test_db):
        await upsert_position("AAPL", 10, 180.0)
        position = (await client.get("/api/portfolio")).json()["positions"][0]
        assert set(position) == {
            "ticker",
            "quantity",
            "avg_cost",
            "current_price",
            "market_value",
            "unrealized_pnl",
            "pnl_percent",
        }

    async def test_trade_response_fields(self, client):
        body = (
            await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "side": "buy", "quantity": 5},
            )
        ).json()
        assert set(body) == {"trade", "cash", "total_value"}
        # The trade bar renders "BUY 5 AAPL @ 190.50" from these four.
        assert {"ticker", "side", "quantity", "price"} <= set(body["trade"])

    async def test_history_snapshot_fields(self, client):
        await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 1},
        )
        snapshot = (await client.get("/api/portfolio/history")).json()["snapshots"][0]
        assert {"total_value", "recorded_at"} <= set(snapshot)

    async def test_trade_errors_are_human_readable(self, client):
        """FastAPI's `detail` is shown verbatim under the trade bar, so it is UI copy."""
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 1000},
        )
        detail = resp.json()["detail"]
        assert resp.status_code == 400
        assert isinstance(detail, str)
        assert detail.startswith("Insufficient cash. Need $")


class TestWatchlistContract:
    async def test_item_fields(self, client):
        item = (await client.get("/api/watchlist")).json()["watchlist"][0]
        assert set(item) == {
            "ticker",
            "price",
            "previous_price",
            "change",
            "change_percent",
            "direction",
        }

    async def test_unpriced_ticker_reports_nulls(self, client, price_cache):
        price_cache.remove("AAPL")
        items = (await client.get("/api/watchlist")).json()["watchlist"]
        aapl = next(item for item in items if item["ticker"] == "AAPL")
        assert aapl["price"] is None
        assert aapl["direction"] is None


class TestChatContract:
    @pytest.fixture(autouse=True)
    def mock_llm(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")

    async def test_response_fields(self, client):
        body = (await client.post("/api/chat", json={"message": "Hello"})).json()
        assert set(body) == {"message", "trades", "watchlist_changes"}
        assert isinstance(body["message"], str)

    async def test_history_message_fields(self, client):
        await client.post("/api/chat", json={"message": "buy 1 AAPL"})
        messages = (await client.get("/api/chat/history")).json()["messages"]

        assert {"id", "role", "content", "actions", "created_at"} <= set(messages[0])
        # `actions` is a JSON string or null — the panel parses it defensively.
        assert messages[0]["actions"] is None
        assert json.loads(messages[-1]["actions"])["trades"]


class TestStreamContract:
    async def test_timestamps_are_unix_seconds(self):
        """Sparkline history is keyed on this; milliseconds would break the charts."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = [c async for c in _generate_events(cache, StubRequest(1), interval=0)]
        payload = json.loads(chunks[1][len("data: ") : -2])
        timestamp = payload["AAPL"]["timestamp"]

        assert timestamp == pytest.approx(time.time(), abs=60)

    async def test_update_fields(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        update = cache.get("AAPL").to_dict()
        assert set(update) == {
            "ticker",
            "price",
            "previous_price",
            "timestamp",
            "change",
            "change_percent",
            "direction",
        }
