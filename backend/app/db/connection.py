"""Database connection management with lazy, idempotent initialization.

Connections are opened per operation and closed immediately. That is the
safest model for a mix of FastAPI request handlers and long-lived background
tasks: an ``aiosqlite`` connection owns a worker thread and is bound to the
event loop that created it, so sharing one across tasks (or across the test
suite's per-test loops) is fragile. SQLite in WAL mode handles the resulting
concurrency, and ``busy_timeout`` absorbs brief writer contention.
"""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .schema import DEFAULT_CASH_BALANCE, DEFAULT_TICKERS, DEFAULT_USER_ID, SCHEMA_SQL

# Default location: <repo>/db/finally.db, which is the Docker volume mount
# target (/app/db) since the backend lives one level below the app root.
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "db" / "finally.db"

_DB_PATH_OVERRIDE: str | None = None

# Paths already initialized in this process, so repeated lazy checks are cheap.
_INITIALIZED: set[str] = set()

BUSY_TIMEOUT_MS = 5000


def get_db_path() -> str:
    """Return the database file path.

    Precedence: explicit :func:`set_db_path` override, then ``FINALLY_DB_PATH``,
    then the repo/container ``db/finally.db``.
    """
    if _DB_PATH_OVERRIDE is not None:
        return _DB_PATH_OVERRIDE
    return os.environ.get("FINALLY_DB_PATH") or str(_DEFAULT_DB_PATH)


def set_db_path(path: str | None) -> None:
    """Override the database path (used by tests). ``None`` clears the override."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = str(path) if path is not None else None
    _INITIALIZED.clear()


async def get_connection() -> aiosqlite.Connection:
    """Open a configured connection to the database.

    The caller owns the connection and must close it. Prefer :func:`connect`.
    """
    path = get_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return db


@asynccontextmanager
async def connect() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager yielding a connection that is always closed."""
    db = await get_connection()
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def transaction() -> AsyncIterator[aiosqlite.Connection]:
    """Yield a connection wrapped in a transaction.

    Commits on success, rolls back on any exception. Use this whenever several
    writes must land together (e.g. cash + position + trade for one order).
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        else:
            await db.commit()


async def init_db() -> None:
    """Create tables and seed default data. Safe to call repeatedly."""
    async with connect() as db:
        await db.executescript(SCHEMA_SQL)

        cursor = await db.execute(
            "SELECT id FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
        )
        user = await cursor.fetchone()

        if user is None:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
                (DEFAULT_USER_ID, DEFAULT_CASH_BALANCE, now),
            )
            for ticker in DEFAULT_TICKERS:
                await db.execute(
                    "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                    "VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), DEFAULT_USER_ID, ticker, now),
                )
            await db.commit()

    _INITIALIZED.add(get_db_path())


async def ensure_initialized() -> None:
    """Initialize the database on first use for the current path.

    Every repository function calls this, so a fresh or empty SQLite file is
    created and seeded lazily on the first request without an explicit
    migration step.
    """
    path = get_db_path()
    if path in _INITIALIZED:
        return
    await init_db()


def _now() -> str:
    """Current UTC time as an ISO-8601 string (the storage format for all timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


__all__ = [
    "BUSY_TIMEOUT_MS",
    "connect",
    "ensure_initialized",
    "get_connection",
    "get_db_path",
    "init_db",
    "set_db_path",
    "transaction",
]
