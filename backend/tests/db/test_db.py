"""Tests for the database layer."""

import asyncio
import json
import os
import uuid
from datetime import datetime

import pytest

from app.db import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_TICKERS,
    DuplicateTickerError,
    InsufficientCashError,
    InsufficientSharesError,
    add_to_watchlist,
    apply_trade,
    connect,
    delete_position,
    get_cash_balance,
    get_chat_history,
    get_db_path,
    get_portfolio_history,
    get_position,
    get_positions,
    get_trades,
    get_watchlist,
    get_watchlist_tickers,
    init_db,
    insert_chat_message,
    insert_snapshot,
    insert_trade,
    remove_from_watchlist,
    set_db_path,
    transaction,
    update_cash_balance,
    upsert_position,
)


@pytest.fixture(autouse=True)
async def temp_db(tmp_path):
    """Use a fresh temporary database for each test."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    await init_db()
    yield db_path
    set_db_path(None)


class TestInitialization:
    async def test_init_creates_default_user(self):
        balance = await get_cash_balance()
        assert balance == DEFAULT_CASH_BALANCE

    async def test_init_seeds_watchlist(self):
        watchlist = await get_watchlist()
        tickers = [entry["ticker"] for entry in watchlist]
        assert sorted(tickers) == sorted(DEFAULT_TICKERS)

    async def test_init_is_idempotent(self):
        await init_db()
        await init_db()
        balance = await get_cash_balance()
        assert balance == DEFAULT_CASH_BALANCE
        watchlist = await get_watchlist()
        assert len(watchlist) == len(DEFAULT_TICKERS)


class TestCashBalance:
    async def test_get_default_balance(self):
        balance = await get_cash_balance()
        assert balance == 10000.0

    async def test_update_balance(self):
        await update_cash_balance(5000.0)
        balance = await get_cash_balance()
        assert balance == 5000.0

    async def test_update_balance_to_zero(self):
        await update_cash_balance(0.0)
        balance = await get_cash_balance()
        assert balance == 0.0


class TestWatchlist:
    async def test_get_default_watchlist(self):
        watchlist = await get_watchlist()
        assert len(watchlist) == 10
        assert all("ticker" in entry for entry in watchlist)

    async def test_add_ticker(self):
        entry = await add_to_watchlist("PYPL")
        assert entry["ticker"] == "PYPL"
        watchlist = await get_watchlist()
        tickers = [e["ticker"] for e in watchlist]
        assert "PYPL" in tickers

    async def test_add_ticker_uppercases(self):
        entry = await add_to_watchlist("pypl")
        assert entry["ticker"] == "PYPL"

    async def test_add_duplicate_raises(self):
        with pytest.raises(Exception):
            await add_to_watchlist("AAPL")

    async def test_remove_ticker(self):
        removed = await remove_from_watchlist("AAPL")
        assert removed is True
        watchlist = await get_watchlist()
        tickers = [e["ticker"] for e in watchlist]
        assert "AAPL" not in tickers

    async def test_remove_nonexistent_returns_false(self):
        removed = await remove_from_watchlist("ZZZZ")
        assert removed is False


class TestPositions:
    async def test_no_positions_initially(self):
        positions = await get_positions()
        assert positions == []

    async def test_upsert_creates_position(self):
        pos = await upsert_position("AAPL", 10, 150.0)
        assert pos["ticker"] == "AAPL"
        assert pos["quantity"] == 10
        assert pos["avg_cost"] == 150.0

    async def test_upsert_updates_existing(self):
        await upsert_position("AAPL", 10, 150.0)
        pos = await upsert_position("AAPL", 20, 155.0)
        assert pos["quantity"] == 20
        assert pos["avg_cost"] == 155.0

        positions = await get_positions()
        assert len(positions) == 1

    async def test_get_position_by_ticker(self):
        await upsert_position("AAPL", 10, 150.0)
        pos = await get_position("AAPL")
        assert pos is not None
        assert pos["ticker"] == "AAPL"

    async def test_get_nonexistent_position(self):
        pos = await get_position("ZZZZ")
        assert pos is None

    async def test_delete_position(self):
        await upsert_position("AAPL", 10, 150.0)
        deleted = await delete_position("AAPL")
        assert deleted is True
        pos = await get_position("AAPL")
        assert pos is None

    async def test_delete_nonexistent_returns_false(self):
        deleted = await delete_position("ZZZZ")
        assert deleted is False


class TestTrades:
    async def test_insert_trade(self):
        trade = await insert_trade("AAPL", "buy", 10, 150.0)
        assert trade["ticker"] == "AAPL"
        assert trade["side"] == "buy"
        assert trade["quantity"] == 10
        assert trade["price"] == 150.0
        assert "id" in trade
        assert "executed_at" in trade

    async def test_trade_ticker_uppercased(self):
        trade = await insert_trade("aapl", "buy", 5, 100.0)
        assert trade["ticker"] == "AAPL"


class TestPortfolioSnapshots:
    async def test_insert_and_get_history(self):
        await insert_snapshot(10000.0)
        await insert_snapshot(10500.0)
        await insert_snapshot(10200.0)

        history = await get_portfolio_history()
        assert len(history) == 3
        assert history[0]["total_value"] == 10000.0
        assert history[2]["total_value"] == 10200.0

    async def test_empty_history(self):
        history = await get_portfolio_history()
        assert history == []


class TestChatMessages:
    async def test_insert_and_get_messages(self):
        await insert_chat_message("user", "Hello")
        await insert_chat_message("assistant", "Hi there!", json.dumps({"trades": []}))

        messages = await get_chat_history()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[0]["actions"] is None
        assert messages[1]["role"] == "assistant"
        assert messages[1]["actions"] is not None

    async def test_chat_history_limit(self):
        for i in range(10):
            await insert_chat_message("user", f"Message {i}")

        messages = await get_chat_history(limit=5)
        assert len(messages) == 5
        # Should be the most recent 5, ordered oldest-first
        assert messages[0]["content"] == "Message 5"
        assert messages[4]["content"] == "Message 9"

    async def test_empty_chat_history(self):
        messages = await get_chat_history()
        assert messages == []


class TestTradeLog:
    async def test_get_trades_empty(self):
        assert await get_trades() == []

    async def test_get_trades_oldest_first(self):
        for i in range(3):
            await insert_trade("AAPL", "buy", i + 1, 100.0 + i)
        trades = await get_trades()
        assert [t["quantity"] for t in trades] == [1, 2, 3]

    async def test_get_trades_limit_returns_most_recent(self):
        for i in range(5):
            await insert_trade("AAPL", "buy", i + 1, 100.0)
        trades = await get_trades(limit=2)
        assert [t["quantity"] for t in trades] == [4, 5]


class TestApplyTrade:
    async def test_buy_updates_cash_position_and_log(self):
        result = await apply_trade("AAPL", "buy", 10, 150.0)

        assert result["cash"] == pytest.approx(10000.0 - 1500.0)
        assert result["position"]["quantity"] == 10
        assert result["position"]["avg_cost"] == pytest.approx(150.0)
        assert result["trade"]["side"] == "buy"

        assert await get_cash_balance() == pytest.approx(8500.0)
        assert len(await get_trades()) == 1

    async def test_buy_averages_cost(self):
        await apply_trade("AAPL", "buy", 10, 100.0)
        result = await apply_trade("AAPL", "buy", 10, 200.0)
        assert result["position"]["quantity"] == 20
        assert result["position"]["avg_cost"] == pytest.approx(150.0)

    async def test_insufficient_cash_leaves_nothing_applied(self):
        with pytest.raises(InsufficientCashError):
            await apply_trade("AAPL", "buy", 1000, 150.0)

        assert await get_cash_balance() == pytest.approx(10000.0)
        assert await get_position("AAPL") is None
        assert await get_trades() == []

    async def test_partial_sell_keeps_avg_cost(self):
        await apply_trade("AAPL", "buy", 10, 100.0)
        result = await apply_trade("AAPL", "sell", 4, 120.0)

        assert result["position"]["quantity"] == pytest.approx(6)
        assert result["position"]["avg_cost"] == pytest.approx(100.0)
        assert result["cash"] == pytest.approx(10000.0 - 1000.0 + 480.0)

    async def test_full_sell_removes_position(self):
        await apply_trade("AAPL", "buy", 10, 100.0)
        result = await apply_trade("AAPL", "sell", 10, 90.0)

        assert result["position"] is None
        assert await get_position("AAPL") is None
        assert await get_cash_balance() == pytest.approx(9900.0)

    async def test_sell_more_than_held_raises(self):
        await apply_trade("AAPL", "buy", 5, 100.0)
        with pytest.raises(InsufficientSharesError):
            await apply_trade("AAPL", "sell", 6, 100.0)

        pos = await get_position("AAPL")
        assert pos["quantity"] == 5
        assert len(await get_trades()) == 1

    async def test_sell_with_no_position_raises(self):
        with pytest.raises(InsufficientSharesError):
            await apply_trade("ZZZZ", "sell", 1, 10.0)

    async def test_fractional_shares(self):
        result = await apply_trade("AAPL", "buy", 0.5, 100.0)
        assert result["position"]["quantity"] == pytest.approx(0.5)
        assert result["cash"] == pytest.approx(9950.0)

    async def test_ticker_and_side_are_normalized(self):
        result = await apply_trade("aapl", "BUY", 1, 100.0)
        assert result["trade"]["ticker"] == "AAPL"
        assert result["trade"]["side"] == "buy"

    async def test_invalid_side_raises_value_error(self):
        with pytest.raises(ValueError):
            await apply_trade("AAPL", "hold", 1, 100.0)

    async def test_non_positive_quantity_raises_value_error(self):
        with pytest.raises(ValueError):
            await apply_trade("AAPL", "buy", 0, 100.0)

    async def test_buy_exactly_all_cash_succeeds(self):
        result = await apply_trade("AAPL", "buy", 100, 100.0)
        assert result["cash"] == pytest.approx(0.0)

    async def test_concurrent_trades_do_not_lose_updates(self):
        results = await asyncio.gather(
            *(apply_trade("AAPL", "buy", 1, 100.0) for _ in range(5))
        )
        assert len(results) == 5
        pos = await get_position("AAPL")
        assert pos["quantity"] == pytest.approx(5)
        assert await get_cash_balance() == pytest.approx(9500.0)
        assert len(await get_trades()) == 5


class TestWatchlistErrors:
    async def test_duplicate_raises_typed_error(self):
        with pytest.raises(DuplicateTickerError):
            await add_to_watchlist("AAPL")

    async def test_duplicate_is_case_insensitive(self):
        with pytest.raises(DuplicateTickerError):
            await add_to_watchlist("aapl")

    async def test_watchlist_tickers_helper(self):
        tickers = await get_watchlist_tickers()
        assert sorted(tickers) == sorted(DEFAULT_TICKERS)


class TestPortfolioHistoryLimit:
    async def test_limit_returns_most_recent_oldest_first(self):
        for value in (100.0, 200.0, 300.0, 400.0):
            await insert_snapshot(value)
        history = await get_portfolio_history(limit=2)
        assert [s["total_value"] for s in history] == [300.0, 400.0]


class TestLazyInitialization:
    async def test_repository_call_initializes_a_fresh_file(self, tmp_path):
        fresh = tmp_path / "nested" / "fresh.db"
        set_db_path(str(fresh))

        # No explicit init_db(): the first repository call must create and seed.
        balance = await get_cash_balance()

        assert fresh.exists()
        assert balance == DEFAULT_CASH_BALANCE
        assert len(await get_watchlist()) == len(DEFAULT_TICKERS)

    async def test_init_preserves_existing_data(self, tmp_path):
        path = str(tmp_path / "persist.db")
        set_db_path(path)
        await init_db()
        await update_cash_balance(1234.0)
        await remove_from_watchlist("AAPL")

        await init_db()

        assert await get_cash_balance() == 1234.0
        assert "AAPL" not in await get_watchlist_tickers()

    async def test_data_persists_across_connections(self, tmp_path):
        path = str(tmp_path / "reopen.db")
        set_db_path(path)
        await apply_trade("MSFT", "buy", 2, 400.0)

        # Every repository call opens a new connection, so this reads from disk.
        set_db_path(path)
        pos = await get_position("MSFT")
        assert pos["quantity"] == 2


class TestDbPath:
    async def test_env_var_is_used_when_no_override(self, tmp_path, monkeypatch):
        set_db_path(None)
        target = str(tmp_path / "from_env.db")
        monkeypatch.setenv("FINALLY_DB_PATH", target)
        assert get_db_path() == target

    async def test_override_wins_over_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "env.db"))
        override = str(tmp_path / "override.db")
        set_db_path(override)
        assert get_db_path() == override

    async def test_default_path_is_the_repo_db_volume(self, monkeypatch):
        set_db_path(None)
        monkeypatch.delenv("FINALLY_DB_PATH", raising=False)
        # <repo>/db/finally.db — the Docker volume mount target (/app/db).
        assert get_db_path().endswith(os.path.join("db", "finally.db"))
        assert os.path.isdir(os.path.dirname(get_db_path()))


class TestSchema:
    async def test_all_tables_exist(self):
        async with connect() as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            names = {row["name"] for row in await cursor.fetchall()}
        assert {
            "users_profile",
            "watchlist",
            "positions",
            "trades",
            "portfolio_snapshots",
            "chat_messages",
        } <= names

    @pytest.mark.parametrize(
        "table", ["watchlist", "positions", "trades", "portfolio_snapshots", "chat_messages"]
    )
    async def test_user_id_defaults_to_default(self, table):
        async with connect() as db:
            cursor = await db.execute(f"PRAGMA table_info({table})")
            cols = {row["name"]: row for row in await cursor.fetchall()}
        assert cols["user_id"]["dflt_value"] == "'default'"

    @pytest.mark.parametrize("table", ["watchlist", "positions"])
    async def test_user_ticker_is_unique(self, table):
        async with connect() as db:
            cursor = await db.execute(f"PRAGMA index_list({table})")
            unique_indexes = [row["name"] for row in await cursor.fetchall() if row["unique"]]
            columns = set()
            for index in unique_indexes:
                cursor = await db.execute(f"PRAGMA index_info({index})")
                columns.add(tuple(row["name"] for row in await cursor.fetchall()))
        assert ("user_id", "ticker") in columns

    async def test_primary_keys_are_uuids(self):
        entry = await add_to_watchlist("PYPL")
        uuid.UUID(entry["id"])

    async def test_timestamps_are_iso_strings(self):
        trade = await insert_trade("AAPL", "buy", 1, 100.0)
        parsed = datetime.fromisoformat(trade["executed_at"])
        assert parsed.tzinfo is not None


class TestTransaction:
    async def test_rolls_back_on_error(self):
        with pytest.raises(RuntimeError):
            async with transaction() as db:
                await db.execute(
                    "UPDATE users_profile SET cash_balance = ? WHERE id = 'default'", (1.0,)
                )
                raise RuntimeError("boom")
        assert await get_cash_balance() == DEFAULT_CASH_BALANCE

    async def test_commits_on_success(self):
        async with transaction() as db:
            await db.execute(
                "UPDATE users_profile SET cash_balance = ? WHERE id = 'default'", (42.0,)
            )
        assert await get_cash_balance() == 42.0
