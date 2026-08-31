"""Database layer for FinAlly.

SQLite with lazy, idempotent initialization: the schema is created and the
default profile/watchlist seeded the first time any repository function runs,
so a fresh volume needs no migration step.

Connections / lifecycle:
    init_db               - Create tables and seed default data (idempotent)
    ensure_initialized    - Lazy init, called automatically by every repository call
    get_db_path           - Resolved SQLite file path
    set_db_path           - Override the DB file path (tests)
    connect               - Async context manager yielding a connection
    transaction           - Async context manager: BEGIN IMMEDIATE / commit / rollback

Users profile:
    get_cash_balance / update_cash_balance

Watchlist:
    get_watchlist / get_watchlist_tickers / add_to_watchlist / remove_from_watchlist

Positions:
    get_positions / get_position / upsert_position / delete_position

Trades:
    insert_trade / get_trades
    apply_trade           - Atomic order: cash + position + trade log in one transaction

Portfolio snapshots:
    insert_snapshot / get_portfolio_history

Chat:
    insert_chat_message / get_chat_history

Errors (all subclass RepositoryError):
    DuplicateTickerError, InsufficientCashError, InsufficientSharesError
"""

from .connection import (
    connect,
    ensure_initialized,
    get_connection,
    get_db_path,
    init_db,
    set_db_path,
    transaction,
)
from .repository import (
    ChatMessage,
    DuplicateTickerError,
    InsufficientCashError,
    InsufficientSharesError,
    Position,
    RepositoryError,
    Side,
    Snapshot,
    Trade,
    TradeResult,
    WatchlistEntry,
    add_to_watchlist,
    apply_trade,
    delete_position,
    get_cash_balance,
    get_chat_history,
    get_portfolio_history,
    get_position,
    get_positions,
    get_trades,
    get_watchlist,
    get_watchlist_tickers,
    insert_chat_message,
    insert_snapshot,
    insert_trade,
    remove_from_watchlist,
    update_cash_balance,
    upsert_position,
)
from .schema import DEFAULT_CASH_BALANCE, DEFAULT_TICKERS, DEFAULT_USER_ID

__all__ = [
    # lifecycle
    "init_db",
    "ensure_initialized",
    "get_connection",
    "get_db_path",
    "set_db_path",
    "connect",
    "transaction",
    # users profile
    "get_cash_balance",
    "update_cash_balance",
    # watchlist
    "get_watchlist",
    "get_watchlist_tickers",
    "add_to_watchlist",
    "remove_from_watchlist",
    # positions
    "get_positions",
    "get_position",
    "upsert_position",
    "delete_position",
    # trades
    "insert_trade",
    "get_trades",
    "apply_trade",
    # snapshots
    "insert_snapshot",
    "get_portfolio_history",
    # chat
    "insert_chat_message",
    "get_chat_history",
    # types
    "WatchlistEntry",
    "Position",
    "Trade",
    "TradeResult",
    "Snapshot",
    "ChatMessage",
    "Side",
    # errors
    "RepositoryError",
    "DuplicateTickerError",
    "InsufficientCashError",
    "InsufficientSharesError",
    # constants
    "DEFAULT_USER_ID",
    "DEFAULT_TICKERS",
    "DEFAULT_CASH_BALANCE",
]
