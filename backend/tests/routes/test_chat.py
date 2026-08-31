"""Tests for chat endpoint."""

import json

import pytest


class TestChat:
    async def test_send_message(self, client):
        resp = await client.post("/api/chat", json={"message": "Hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert data["trades"] == []
        assert data["watchlist_changes"] == []

    async def test_chat_history(self, client):
        await client.post("/api/chat", json={"message": "Hello"})
        resp = await client.get("/api/chat/history")
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert len(messages) == 2  # user + assistant
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"


class TestChatActionsReachTheMarketSource:
    """PLAN.md §9: actions the assistant takes must be fully applied, including
    starting/stopping the price feed for tickers it adds or drops."""

    @pytest.fixture(autouse=True)
    def mock_llm(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")

    async def test_watchlist_add_starts_streaming(self, client, market_source, price_cache):
        resp = await client.post(
            "/api/chat", json={"message": "add PYPL to my watchlist"}
        )
        assert resp.status_code == 200
        assert resp.json()["watchlist_changes"][0]["ticker"] == "PYPL"

        assert "PYPL" in market_source.get_tickers()
        assert price_cache.get_price("PYPL") is not None

        listed = await client.get("/api/watchlist")
        assert "PYPL" in [item["ticker"] for item in listed.json()["watchlist"]]

    async def test_watchlist_remove_stops_streaming(self, client, market_source, price_cache):
        resp = await client.post("/api/chat", json={"message": "unwatch GOOGL"})
        assert resp.status_code == 200

        assert "GOOGL" not in market_source.get_tickers()
        assert price_cache.get_price("GOOGL") is None

    async def test_removing_a_held_ticker_keeps_it_streaming(
        self, client, market_source, price_cache
    ):
        await client.post(
            "/api/portfolio/trade",
            json={"ticker": "GOOGL", "side": "buy", "quantity": 1},
        )
        await client.post("/api/chat", json={"message": "unwatch GOOGL"})

        assert "GOOGL" in market_source.get_tickers()
        assert price_cache.get_price("GOOGL") == 175.25

    async def test_chat_trade_is_persisted_and_logged(self, client):
        resp = await client.post("/api/chat", json={"message": "buy 2 AAPL"})
        assert resp.json()["trades"][0]["status"] == "executed"

        portfolio = (await client.get("/api/portfolio")).json()
        assert portfolio["positions"][0]["ticker"] == "AAPL"
        assert portfolio["cash"] == 9619.0  # 10000 - 2 * 190.50

        history = (await client.get("/api/chat/history")).json()["messages"]
        assert json.loads(history[-1]["actions"])["trades"][0]["ticker"] == "AAPL"

    async def test_failed_chat_trade_reports_the_error(self, client):
        resp = await client.post("/api/chat", json={"message": "buy 100000 AAPL"})
        assert "Insufficient cash" in resp.json()["trades"][0]["error"]
        assert (await client.get("/api/portfolio")).json()["cash"] == 10000.0
