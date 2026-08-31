# database-engineer — updated 2026-08-31T00:00:00Z
State: done
Done:
- Audited `backend/app/db/**` against PLAN.md §7: all six tables, `user_id` DEFAULT 'default',
  UNIQUE(user_id, ticker) on watchlist + positions, UUID text PKs, ISO-8601 UTC TEXT timestamps. Schema was correct; left as-is.
- Rewrote `connection.py`: lazy idempotent init, dir-creating path resolution, WAL + busy_timeout,
  `connect()` / `transaction()` context managers.
- Rewrote `repository.py`: TypedDict row types, typed errors, atomic `apply_trade`, `get_trades`,
  `get_watchlist_tickers`, `get_portfolio_history(limit)`.
- `uv run pytest` — 357 passed team-wide (64 in tests/db).
In flight: nothing
Blocked on: nothing
Interface changes: additive only; every previously exported function keeps its name, signature and
return shape. Two notes below marked (NEW) and (CHANGED).

## Repository API — `from app.db import ...`

All functions are async, take a trailing optional `user_id: str = "default"`, and lazily create +
seed the database on first use. No caller ever needs to call `init_db()` first (main.py still may).

### Lifecycle
- `init_db() -> None` — create tables, seed default profile + 10 tickers. Idempotent; never
  overwrites existing data.
- `ensure_initialized() -> None` — cheap lazy init; called automatically by every function below.
- `get_db_path() -> str` — override > `FINALLY_DB_PATH` env > `<repo>/db/finally.db`
  (= `/app/db/finally.db` in the container). Parent directory is created on connect.
- `set_db_path(path: str | None) -> None` — test override; `None` clears it.
- `connect()` — async CM yielding a configured `aiosqlite.Connection` (row_factory=Row, WAL,
  foreign_keys=ON, busy_timeout=5000), always closed.
- `transaction()` — async CM: `BEGIN IMMEDIATE`, commit on success, rollback on exception.

Connections are opened per operation and closed immediately — safe to call from request handlers
and background tasks concurrently, with no shared connection bound to one event loop.

### Cash
- `get_cash_balance() -> float`
- `update_cash_balance(amount: float) -> float`

### Watchlist
- `get_watchlist() -> list[WatchlistEntry]`  # {id, ticker, added_at}, oldest first
- `get_watchlist_tickers() -> list[str]`  (NEW)
- `add_to_watchlist(ticker) -> WatchlistEntry` — uppercases; raises `DuplicateTickerError` (CHANGED:
  was a bare `aiosqlite.IntegrityError`; the new error subclasses `RepositoryError`, so
  `except Exception` in routes/watchlist.py still works — `except DuplicateTickerError` is cleaner).
- `remove_from_watchlist(ticker) -> bool`

### Positions
- `get_positions() -> list[Position]`  # {id, ticker, quantity, avg_cost, updated_at}
- `get_position(ticker) -> Position | None`
- `upsert_position(ticker, quantity, avg_cost) -> Position`
- `delete_position(ticker) -> bool`

### Trades
- `insert_trade(ticker, side, quantity, price) -> Trade`  # {id, user_id, ticker, side, quantity, price, executed_at}
- `get_trades(limit: int = 100) -> list[Trade]` — most recent N, returned oldest-first. (NEW)
- `apply_trade(ticker, side, quantity, price) -> TradeResult` (NEW) — **recommended for
  `POST /api/portfolio/trade` and for LLM auto-execution.** One `BEGIN IMMEDIATE` transaction:
  validates funds/shares, adjusts cash, updates weighted avg cost on buys, keeps avg cost on sells,
  deletes the position at zero, and appends the trade. Returns
  `{"trade": Trade, "cash": float, "position": Position | None}`.
  Raises `InsufficientCashError` / `InsufficientSharesError` (message is user-safe, suitable for an
  HTTP 400 `detail` or an LLM error string) and `ValueError` for a bad side/quantity/price.
  On any failure nothing is written. It does **not** record a portfolio snapshot — the caller still
  computes total value from the price cache and calls `insert_snapshot`.
  **Adopted:** `app/routes/trading.py::execute_trade_order` now delegates to it and maps the errors
  onto `TradeError`, so manual and LLM-initiated trades share one transactional path.

### Snapshots
- `insert_snapshot(total_value) -> Snapshot`  # {id, total_value, recorded_at}
- `get_portfolio_history(limit: int | None = None) -> list[Snapshot]` — oldest-first; with `limit`,
  the most recent N still oldest-first. (`limit` is NEW; default behaviour unchanged.)

### Chat
- `insert_chat_message(role, content, actions: str | None = None) -> ChatMessage`
- `get_chat_history(limit: int = 50) -> list[ChatMessage]` — most recent N, oldest-first.

### Types and errors
`WatchlistEntry`, `Position`, `Trade`, `TradeResult`, `Snapshot`, `ChatMessage` (TypedDicts, so
existing dict-style access is unchanged), `Side = Literal["buy","sell"]`.
`RepositoryError` <- `DuplicateTickerError`, `InsufficientCashError`, `InsufficientSharesError`.
Constants: `DEFAULT_USER_ID`, `DEFAULT_TICKERS`, `DEFAULT_CASH_BALANCE`.
