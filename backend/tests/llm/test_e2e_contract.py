"""The exact LLM_MOCK contract that test/e2e/chat.spec.ts depends on.

Each test here mirrors one input the Playwright suite sends and one assertion it
makes. If someone reweords a mock reply or changes a trigger, this fails in the
fast backend suite instead of surfacing as a mysterious browser-test failure.

Keep this file in sync with integration-tester before changing `app/llm/mock.py`.
"""

import re

import pytest

from app.db import init_db, set_db_path
from app.llm.service import chat_with_llm
from app.market import PriceCache

SEED_PRICES = {
    "AAPL": 190.50, "GOOGL": 175.25, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 880.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 620.00,
}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")


@pytest.fixture
async def test_db(tmp_path):
    set_db_path(str(tmp_path / "test.db"))
    await init_db()
    yield
    set_db_path(str(tmp_path / "unused.db"))


@pytest.fixture
def price_cache():
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)
    return cache


class TestE2EChatContract:
    async def test_1_greeting(self, test_db, price_cache):
        result = await chat_with_llm("hello", price_cache)
        assert "trading assistant" in result["message"]

    async def test_2_portfolio_question(self, test_db, price_cache):
        result = await chat_with_llm("how is my portfolio doing?", price_cache)
        assert "portfolio is worth" in result["message"] or "in cash" in result["message"]

    async def test_3_buy(self, test_db, price_cache):
        result = await chat_with_llm("buy 2 NVDA", price_cache)
        assert result["trades"] == [{
            "ticker": "NVDA", "side": "buy", "quantity": 2.0,
            "price": 880.00, "status": "executed",
        }]

    async def test_4_sell(self, test_db, price_cache):
        await chat_with_llm("buy 2 NVDA", price_cache)
        result = await chat_with_llm("sell 1 NVDA", price_cache)
        assert result["trades"][0]["ticker"] == "NVDA"
        assert result["trades"][0]["side"] == "sell"
        assert result["trades"][0]["quantity"] == 1.0
        assert result["trades"][0]["status"] == "executed"

    async def test_5_watchlist_add(self, test_db, price_cache):
        result = await chat_with_llm("add PYPL to watchlist", price_cache)
        assert result["watchlist_changes"] == [
            {"ticker": "PYPL", "action": "add", "status": "done"}
        ]

    async def test_6_sell_without_a_position_reports_insufficient_shares(
        self, test_db, price_cache
    ):
        result = await chat_with_llm("sell 500 TSLA", price_cache)
        entry = result["trades"][0]
        assert "status" not in entry
        assert re.search(r"insufficient shares", entry["error"], re.I)


class TestResponseShapeContract:
    """The frontend branches on these keys; they must not change silently."""

    async def test_executed_trade_carries_status_and_price(self, test_db, price_cache):
        entry = (await chat_with_llm("buy 1 AAPL", price_cache))["trades"][0]
        assert entry["status"] == "executed"
        assert entry["price"] == 190.50
        assert "error" not in entry

    async def test_failed_trade_carries_error_and_no_status(self, test_db, price_cache):
        entry = (await chat_with_llm("buy 100000 AAPL", price_cache))["trades"][0]
        assert "error" in entry
        assert "status" not in entry

    async def test_top_level_keys(self, test_db, price_cache):
        result = await chat_with_llm("hello", price_cache)
        assert set(result) == {"message", "trades", "watchlist_changes"}


class TestNoInputCrashes:
    """Truncated trigger words must fall through to the greeting, never 500."""

    @pytest.mark.parametrize(
        "message",
        ["watch ", "watch", "add ", "buy ", "sell ", "add  to watchlist", "", "   "],
    )
    async def test_partial_triggers_are_safe(self, test_db, price_cache, message):
        result = await chat_with_llm(message, price_cache)
        assert "trading assistant" in result["message"]
        assert result["trades"] == []
        assert result["watchlist_changes"] == []


class TestIntegerQuantitiesStillWork:
    """Decimal support must not have broken plain integer quantities."""

    @pytest.mark.parametrize("qty,expected", [("500", 500.0), ("1", 1.0), ("2.5", 2.5)])
    async def test_quantities(self, test_db, price_cache, qty, expected):
        result = await chat_with_llm(f"sell {qty} TSLA", price_cache)
        assert result["trades"][0]["quantity"] == expected
