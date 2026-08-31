"""Typed CRUD operations for every FinAlly table.

All functions are async, take an optional ``user_id`` (defaulting to the
single-user ``"default"`` profile), and lazily initialize the database on first
use. Route handlers and the LLM layer call these instead of writing SQL.
"""

from typing import Literal, TypedDict

import aiosqlite

from .connection import _now, _uuid, connect, ensure_initialized, transaction
from .schema import DEFAULT_USER_ID

Side = Literal["buy", "sell"]


# --- Row types -------------------------------------------------------------


class WatchlistEntry(TypedDict):
    id: str
    ticker: str
    added_at: str


class Position(TypedDict):
    id: str
    ticker: str
    quantity: float
    avg_cost: float
    updated_at: str


class Trade(TypedDict):
    id: str
    user_id: str
    ticker: str
    side: str
    quantity: float
    price: float
    executed_at: str


class Snapshot(TypedDict):
    id: str
    total_value: float
    recorded_at: str


class ChatMessage(TypedDict):
    id: str
    role: str
    content: str
    actions: str | None
    created_at: str


class TradeResult(TypedDict):
    """Outcome of :func:`apply_trade`."""

    trade: Trade
    cash: float
    position: Position | None


# --- Errors ----------------------------------------------------------------


class RepositoryError(Exception):
    """Base class for expected, caller-recoverable repository failures."""


class DuplicateTickerError(RepositoryError):
    """The ticker is already on the watchlist."""


class InsufficientCashError(RepositoryError):
    """The buy costs more than the available cash balance."""


class InsufficientSharesError(RepositoryError):
    """The sell exceeds the quantity held."""


# --- Users Profile ---------------------------------------------------------


async def get_cash_balance(user_id: str = DEFAULT_USER_ID) -> float:
    """Get the user's cash balance (0.0 if the profile does not exist)."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return float(row["cash_balance"]) if row else 0.0


async def update_cash_balance(amount: float, user_id: str = DEFAULT_USER_ID) -> float:
    """Set the user's cash balance to ``amount``. Returns the new balance."""
    await ensure_initialized()
    async with connect() as db:
        await db.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (amount, user_id)
        )
        await db.commit()
        return amount


# --- Watchlist -------------------------------------------------------------


async def get_watchlist(user_id: str = DEFAULT_USER_ID) -> list[WatchlistEntry]:
    """All watchlist entries, oldest first."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]  # type: ignore[misc]


async def get_watchlist_tickers(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Just the ticker symbols, oldest first."""
    return [entry["ticker"] for entry in await get_watchlist(user_id)]


async def add_to_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> WatchlistEntry:
    """Add a ticker to the watchlist.

    Raises :class:`DuplicateTickerError` if the ticker is already watched.
    """
    await ensure_initialized()
    ticker = ticker.strip().upper()
    entry: WatchlistEntry = {"id": _uuid(), "ticker": ticker, "added_at": _now()}
    async with connect() as db:
        try:
            await db.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (entry["id"], user_id, ticker, entry["added_at"]),
            )
        except aiosqlite.IntegrityError as exc:
            raise DuplicateTickerError(f"{ticker} is already on the watchlist") from exc
        await db.commit()
        return entry


async def remove_from_watchlist(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Remove a ticker. Returns True if a row was deleted."""
    await ensure_initialized()
    ticker = ticker.strip().upper()
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Positions -------------------------------------------------------------


async def get_positions(user_id: str = DEFAULT_USER_ID) -> list[Position]:
    """All positions, ordered by ticker."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]  # type: ignore[misc]


async def get_position(ticker: str, user_id: str = DEFAULT_USER_ID) -> Position | None:
    """A single position by ticker, or None."""
    await ensure_initialized()
    async with connect() as db:
        return await _get_position(db, ticker.strip().upper(), user_id)


async def upsert_position(
    ticker: str, quantity: float, avg_cost: float, user_id: str = DEFAULT_USER_ID
) -> Position:
    """Create or update a position."""
    await ensure_initialized()
    async with connect() as db:
        position = await _upsert_position(db, ticker.strip().upper(), quantity, avg_cost, user_id)
        await db.commit()
        return position


async def delete_position(ticker: str, user_id: str = DEFAULT_USER_ID) -> bool:
    """Delete a position. Returns True if a row was deleted."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker.strip().upper()),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Trades ----------------------------------------------------------------


async def insert_trade(
    ticker: str, side: str, quantity: float, price: float, user_id: str = DEFAULT_USER_ID
) -> Trade:
    """Append a trade to the trade log."""
    await ensure_initialized()
    async with connect() as db:
        trade = await _insert_trade(db, ticker.strip().upper(), side, quantity, price, user_id)
        await db.commit()
        return trade


async def get_trades(limit: int = 100, user_id: str = DEFAULT_USER_ID) -> list[Trade]:
    """Most recent trades, returned oldest-first."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, user_id, ticker, side, quantity, price, executed_at FROM trades "
            "WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]  # type: ignore[misc]


async def apply_trade(
    ticker: str,
    side: Side,
    quantity: float,
    price: float,
    user_id: str = DEFAULT_USER_ID,
) -> TradeResult:
    """Execute a market order atomically.

    Adjusts cash, upserts or deletes the position, and appends the trade in a
    single transaction, so a failure part-way through leaves nothing applied.
    Buys update the weighted average cost; sells leave it unchanged and drop the
    position once the quantity reaches zero. Because fractional shares accumulate
    float error, quantities are compared with a 1e-9 tolerance: a sell of the full
    holding deletes the position rather than leaving a dust remainder, and a sell
    that overshoots by less than the tolerance is allowed. Callers depend on the
    no-dust guarantee (see tests/routes/test_trading.py), so treat the tolerance as
    part of this contract.

    Raises :class:`InsufficientCashError` or :class:`InsufficientSharesError`
    when the order cannot be filled, and ``ValueError`` for a bad side/quantity.
    """
    await ensure_initialized()
    ticker = ticker.strip().upper()
    side = side.lower()  # type: ignore[assignment]
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    async with transaction() as db:
        cursor = await db.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        cash = float(row["cash_balance"]) if row else 0.0
        existing = await _get_position(db, ticker, user_id)
        notional = price * quantity

        if side == "buy":
            if notional > cash + 1e-9:
                raise InsufficientCashError(
                    f"Insufficient cash. Need ${notional:.2f}, have ${cash:.2f}"
                )
            new_cash = cash - notional
            if existing:
                total_qty = existing["quantity"] + quantity
                total_cost = existing["avg_cost"] * existing["quantity"] + notional
                position = await _upsert_position(
                    db, ticker, total_qty, total_cost / total_qty, user_id
                )
            else:
                position = await _upsert_position(db, ticker, quantity, price, user_id)
        else:
            held = existing["quantity"] if existing else 0.0
            if quantity > held + 1e-9:
                raise InsufficientSharesError(
                    f"Insufficient shares. Have {held}, trying to sell {quantity}"
                )
            new_cash = cash + notional
            remaining = held - quantity
            if remaining > 1e-9:
                position = await _upsert_position(
                    db, ticker, remaining, existing["avg_cost"], user_id
                )
            else:
                await db.execute(
                    "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
                )
                position = None

        await db.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id)
        )
        trade = await _insert_trade(db, ticker, side, quantity, price, user_id)

    return {"trade": trade, "cash": new_cash, "position": position}


# --- Portfolio Snapshots ---------------------------------------------------


async def insert_snapshot(total_value: float, user_id: str = DEFAULT_USER_ID) -> Snapshot:
    """Record a portfolio value snapshot."""
    await ensure_initialized()
    snapshot: Snapshot = {"id": _uuid(), "total_value": total_value, "recorded_at": _now()}
    async with connect() as db:
        await db.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (snapshot["id"], user_id, total_value, snapshot["recorded_at"]),
        )
        await db.commit()
        return snapshot


async def get_portfolio_history(
    limit: int | None = None, user_id: str = DEFAULT_USER_ID
) -> list[Snapshot]:
    """Snapshots oldest-first. With ``limit``, the most recent N, still oldest-first."""
    await ensure_initialized()
    async with connect() as db:
        if limit is None:
            cursor = await db.execute(
                "SELECT id, total_value, recorded_at FROM portfolio_snapshots "
                "WHERE user_id = ? ORDER BY recorded_at, rowid",
                (user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]  # type: ignore[misc]

        cursor = await db.execute(
            "SELECT id, total_value, recorded_at FROM portfolio_snapshots "
            "WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]  # type: ignore[misc]


# --- Chat Messages ---------------------------------------------------------


async def insert_chat_message(
    role: str, content: str, actions: str | None = None, user_id: str = DEFAULT_USER_ID
) -> ChatMessage:
    """Store a chat message. ``actions`` is a JSON string or None."""
    await ensure_initialized()
    msg: ChatMessage = {
        "id": _uuid(),
        "role": role,
        "content": content,
        "actions": actions,
        "created_at": _now(),
    }
    async with connect() as db:
        await db.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg["id"], user_id, role, content, actions, msg["created_at"]),
        )
        await db.commit()
        return msg


async def get_chat_history(limit: int = 50, user_id: str = DEFAULT_USER_ID) -> list[ChatMessage]:
    """The most recent ``limit`` messages, returned oldest-first."""
    await ensure_initialized()
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]  # type: ignore[misc]


# --- Internal helpers (operate on a caller-owned connection) ---------------


async def _get_position(
    db: aiosqlite.Connection, ticker: str, user_id: str
) -> Position | None:
    cursor = await db.execute(
        "SELECT id, ticker, quantity, avg_cost, updated_at FROM positions "
        "WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None  # type: ignore[return-value]


async def _upsert_position(
    db: aiosqlite.Connection, ticker: str, quantity: float, avg_cost: float, user_id: str
) -> Position:
    now = _now()
    cursor = await db.execute(
        "SELECT id FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
    )
    existing = await cursor.fetchone()
    if existing:
        pos_id = existing["id"]
        await db.execute(
            "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? WHERE id = ?",
            (quantity, avg_cost, now, pos_id),
        )
    else:
        pos_id = _uuid()
        await db.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pos_id, user_id, ticker, quantity, avg_cost, now),
        )
    return {
        "id": pos_id,
        "ticker": ticker,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "updated_at": now,
    }


async def _insert_trade(
    db: aiosqlite.Connection, ticker: str, side: str, quantity: float, price: float, user_id: str
) -> Trade:
    trade: Trade = {
        "id": _uuid(),
        "user_id": user_id,
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": price,
        "executed_at": _now(),
    }
    await db.execute(
        "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trade["id"], user_id, ticker, side, quantity, price, trade["executed_at"]),
    )
    return trade
