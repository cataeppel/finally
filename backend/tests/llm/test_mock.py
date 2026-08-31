"""Tests for the mock LLM module.

These lock in the trigger phrases the E2E suite relies on: if a trigger changes,
these tests fail before the Playwright run does.
"""

import pytest

from app.llm.mock import mock_chat

EMPTY_CONTEXT = {"cash": 10000.0, "positions": [], "total_value": 10000.0}


class TestGreeting:
    def test_greeting(self):
        result = mock_chat("hello", EMPTY_CONTEXT)
        assert "FinAlly" in result.message
        assert "trading assistant" in result.message  # asserted by test/e2e/chat.spec.ts
        assert result.trades == []
        assert result.watchlist_changes == []

    @pytest.mark.parametrize("message", ["", "   ", "what is the weather"])
    def test_unrecognized_falls_back_to_greeting(self, message):
        assert "trading assistant" in mock_chat(message, EMPTY_CONTEXT).message

    def test_is_deterministic(self):
        first = mock_chat("buy 10 AAPL", EMPTY_CONTEXT)
        second = mock_chat("buy 10 AAPL", EMPTY_CONTEXT)
        assert first.model_dump() == second.model_dump()


class TestBuy:
    @pytest.mark.parametrize(
        "message,ticker,quantity",
        [
            ("buy 10 AAPL", "AAPL", 10),
            ("buy 5 shares of MSFT", "MSFT", 5),
            ("buy 3 share of nvda", "NVDA", 3),
            ("please buy 2 TSLA for me", "TSLA", 2),
            ("buy 1.5 GOOGL", "GOOGL", 1.5),
        ],
    )
    def test_buy_variants(self, message, ticker, quantity):
        result = mock_chat(message, EMPTY_CONTEXT)
        assert len(result.trades) == 1
        assert result.trades[0].ticker == ticker
        assert result.trades[0].side == "buy"
        assert result.trades[0].quantity == quantity
        assert result.watchlist_changes == []


class TestSell:
    @pytest.mark.parametrize(
        "message,ticker,quantity",
        [
            ("sell 3 GOOGL", "GOOGL", 3),
            ("sell 10 shares of AAPL", "AAPL", 10),
            ("sell 0.5 nvda", "NVDA", 0.5),
        ],
    )
    def test_sell_variants(self, message, ticker, quantity):
        result = mock_chat(message, EMPTY_CONTEXT)
        assert len(result.trades) == 1
        assert result.trades[0].ticker == ticker
        assert result.trades[0].side == "sell"
        assert result.trades[0].quantity == quantity


class TestWatchlist:
    @pytest.mark.parametrize(
        "message,ticker",
        [
            ("watch PYPL", "PYPL"),
            ("add TSLA to my watchlist", "TSLA"),
            ("add pypl to the watchlist", "PYPL"),
        ],
    )
    def test_add(self, message, ticker):
        result = mock_chat(message, EMPTY_CONTEXT)
        assert len(result.watchlist_changes) == 1
        assert result.watchlist_changes[0].ticker == ticker
        assert result.watchlist_changes[0].action == "add"
        assert result.trades == []

    @pytest.mark.parametrize(
        "message,ticker",
        [
            ("unwatch AAPL", "AAPL"),
            ("remove AAPL from my watchlist", "AAPL"),
            ("stop watching NFLX", "NFLX"),
        ],
    )
    def test_remove(self, message, ticker):
        result = mock_chat(message, EMPTY_CONTEXT)
        assert len(result.watchlist_changes) == 1
        assert result.watchlist_changes[0].ticker == ticker
        assert result.watchlist_changes[0].action == "remove"

    def test_remove_is_not_misread_as_add(self):
        result = mock_chat("remove AAPL from my watchlist", EMPTY_CONTEXT)
        assert result.watchlist_changes[0].action == "remove"


class TestPortfolioAnalysis:
    def test_no_positions(self):
        result = mock_chat("show my portfolio", EMPTY_CONTEXT)
        assert "10,000.00" in result.message
        assert "in cash" in result.message  # asserted by test/e2e/chat.spec.ts
        assert result.trades == []

    def test_with_positions(self):
        context = {
            "cash": 5000.0,
            "positions": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "total_value": 8000.0,
        }
        result = mock_chat("analyze my portfolio", context)
        assert "portfolio is worth" in result.message  # asserted by chat.spec.ts
        assert "8,000.00" in result.message
        assert "AAPL" in result.message
        assert "MSFT" in result.message

    @pytest.mark.parametrize(
        "message",
        ["show my portfolio", "what are my positions", "list my holdings", "what is my p&l"],
    )
    def test_keywords(self, message):
        result = mock_chat(message, EMPTY_CONTEXT)
        assert "in cash" in result.message
