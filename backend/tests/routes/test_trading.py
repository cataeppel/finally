"""Tests for the shared trade execution service (PLAN.md §8, §9)."""

import pytest

from app.db import (
    add_to_watchlist,
    get_cash_balance,
    get_portfolio_history,
    get_position,
    remove_from_watchlist,
    upsert_position,
)
from app.routes.trading import (
    TradeError,
    execute_trade_order,
    normalize_ticker,
    record_snapshot,
    sync_tracked_tickers,
    value_portfolio,
)


class TestNormalizeTicker:
    @pytest.mark.parametrize(
        "raw,expected",
        [("aapl", "AAPL"), ("  msft  ", "MSFT"), ("brk.b", "BRK.B"), ("A", "A")],
    )
    def test_accepts_and_uppercases(self, raw, expected):
        assert normalize_ticker(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "123", "AAPL!", "TOOLONGTICKER", "9F"])
    def test_rejects_junk(self, raw):
        with pytest.raises(TradeError):
            normalize_ticker(raw)


class TestExecuteTradeOrder:
    async def test_buy_debits_cash_and_opens_position(self, test_db, price_cache):
        result = await execute_trade_order("aapl", "BUY", 5, price_cache)

        assert result["trade"]["ticker"] == "AAPL"
        assert result["trade"]["side"] == "buy"
        assert result["trade"]["price"] == 190.50
        assert result["cash"] == 9047.5

        position = await get_position("AAPL")
        assert position["quantity"] == 5
        assert position["avg_cost"] == 190.50

    async def test_buy_spending_entire_balance_is_allowed(self, test_db, price_cache):
        """Float residue must not turn an exact-cash buy into 'insufficient cash'."""
        cash = await get_cash_balance()
        quantity = cash / 190.50

        result = await execute_trade_order("AAPL", "buy", quantity, price_cache)

        assert result["cash"] == pytest.approx(0.0, abs=0.01)

    async def test_sell_credits_cash_and_reduces_position(self, test_db, price_cache):
        await upsert_position("AAPL", 10, 180.0)

        result = await execute_trade_order("AAPL", "sell", 4, price_cache)

        assert result["cash"] == pytest.approx(10000 + 4 * 190.50)
        assert (await get_position("AAPL"))["quantity"] == 6

    async def test_sell_keeps_avg_cost_unchanged(self, test_db, price_cache):
        await upsert_position("AAPL", 10, 180.0)
        await execute_trade_order("AAPL", "sell", 4, price_cache)
        assert (await get_position("AAPL"))["avg_cost"] == 180.0

    async def test_selling_whole_position_deletes_it(self, test_db, price_cache):
        await upsert_position("AAPL", 5, 180.0)
        await execute_trade_order("AAPL", "sell", 5, price_cache)
        assert await get_position("AAPL") is None

    async def test_fractional_sell_to_zero_leaves_no_dust(self, test_db, price_cache):
        """0.1 * 3 != 0.3 in binary floating point; the position must still close."""
        await upsert_position("AAPL", 0.1 + 0.1 + 0.1, 180.0)
        await execute_trade_order("AAPL", "sell", 0.3, price_cache)
        assert await get_position("AAPL") is None

    async def test_buy_averages_cost_across_fills(self, test_db, price_cache):
        await execute_trade_order("AAPL", "buy", 10, price_cache)
        price_cache.update("AAPL", 200.50)
        await execute_trade_order("AAPL", "buy", 10, price_cache)

        position = await get_position("AAPL")
        assert position["quantity"] == 20
        assert position["avg_cost"] == pytest.approx(195.50)

    async def test_insufficient_cash_is_rejected_without_side_effects(
        self, test_db, price_cache
    ):
        with pytest.raises(TradeError, match="Insufficient cash"):
            await execute_trade_order("AAPL", "buy", 1000, price_cache)

        assert await get_cash_balance() == 10000.0
        assert await get_position("AAPL") is None

    async def test_insufficient_shares_is_rejected_without_side_effects(
        self, test_db, price_cache
    ):
        await upsert_position("AAPL", 2, 180.0)

        with pytest.raises(TradeError, match="Insufficient shares"):
            await execute_trade_order("AAPL", "sell", 5, price_cache)

        assert await get_cash_balance() == 10000.0
        assert (await get_position("AAPL"))["quantity"] == 2

    async def test_unknown_ticker_has_no_price(self, test_db, price_cache):
        with pytest.raises(TradeError, match="No price available"):
            await execute_trade_order("ZZZZ", "buy", 1, price_cache)

    @pytest.mark.parametrize("side", ["short", "", "hold"])
    async def test_invalid_side(self, test_db, price_cache, side):
        with pytest.raises(TradeError, match="side must be"):
            await execute_trade_order("AAPL", side, 1, price_cache)

    @pytest.mark.parametrize("quantity", [0, -5, float("nan")])
    async def test_invalid_quantity(self, test_db, price_cache, quantity):
        with pytest.raises(TradeError, match="quantity must be positive"):
            await execute_trade_order("AAPL", "buy", quantity, price_cache)

    async def test_records_snapshot_after_each_trade(self, test_db, price_cache):
        await execute_trade_order("AAPL", "buy", 1, price_cache)
        await execute_trade_order("AAPL", "sell", 1, price_cache)

        history = await get_portfolio_history()
        assert len(history) == 2

    async def test_failed_trade_records_no_snapshot(self, test_db, price_cache):
        with pytest.raises(TradeError):
            await execute_trade_order("AAPL", "buy", 1000, price_cache)
        assert await get_portfolio_history() == []


class TestValuePortfolio:
    async def test_empty_portfolio_is_all_cash(self, test_db, price_cache):
        valuation = await value_portfolio(price_cache)
        assert valuation == {
            "positions": [],
            "cash": 10000.0,
            "total_market_value": 0.0,
            "total_value": 10000.0,
            "unrealized_pnl": 0.0,
        }

    async def test_marks_positions_to_market(self, test_db, price_cache):
        await upsert_position("AAPL", 10, 180.0)
        valuation = await value_portfolio(price_cache)

        position = valuation["positions"][0]
        assert position["current_price"] == 190.50
        assert position["market_value"] == 1905.0
        assert position["unrealized_pnl"] == 105.0
        assert position["pnl_percent"] == pytest.approx(5.83, abs=0.01)
        assert valuation["total_value"] == 11905.0
        assert valuation["unrealized_pnl"] == 105.0

    async def test_falls_back_to_avg_cost_without_a_live_price(
        self, test_db, price_cache
    ):
        await upsert_position("ZZZZ", 10, 50.0)
        valuation = await value_portfolio(price_cache)

        position = valuation["positions"][0]
        assert position["current_price"] == 50.0
        assert position["unrealized_pnl"] == 0.0

    async def test_record_snapshot_persists_total_value(self, test_db, price_cache):
        await upsert_position("AAPL", 10, 180.0)

        total = await record_snapshot(price_cache)

        history = await get_portfolio_history()
        assert total == 11905.0
        assert len(history) == 1
        assert history[0]["total_value"] == 11905.0


class TestSyncTrackedTickers:
    async def test_starts_streaming_newly_watched_tickers(
        self, test_db, price_cache, market_source
    ):
        await add_to_watchlist("PYPL")
        await sync_tracked_tickers(market_source, price_cache)

        assert "PYPL" in market_source.get_tickers()
        assert price_cache.get_price("PYPL") is not None

    async def test_stops_streaming_unwatched_tickers(
        self, test_db, price_cache, market_source
    ):
        await remove_from_watchlist("AAPL")
        await sync_tracked_tickers(market_source, price_cache)

        assert "AAPL" not in market_source.get_tickers()
        assert price_cache.get_price("AAPL") is None

    async def test_keeps_streaming_unwatched_holdings(
        self, test_db, price_cache, market_source
    ):
        await upsert_position("AAPL", 3, 180.0)
        await remove_from_watchlist("AAPL")
        await sync_tracked_tickers(market_source, price_cache)

        assert "AAPL" in market_source.get_tickers()
        assert price_cache.get_price("AAPL") == 190.50

    async def test_is_idempotent(self, test_db, price_cache, market_source):
        before = sorted(market_source.get_tickers())
        await sync_tracked_tickers(market_source, price_cache)
        await sync_tracked_tickers(market_source, price_cache)
        assert sorted(market_source.get_tickers()) == before
