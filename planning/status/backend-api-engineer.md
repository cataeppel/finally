# Backend API Engineer — updated 2026-08-31T16:00Z
State: done

Done:
- Audited the prior pass; `cd backend && uv run pytest` green throughout (337 passing at last run).
- New `backend/app/routes/trading.py`: single source of truth for trade validation,
  portfolio valuation, snapshots and market-source reconciliation. No SQL — it calls
  the repository, and the order itself goes through the atomic `db.apply_trade`.
- `POST /api/portfolio/trade` and `GET /api/portfolio` now use it. Fixes: ticker
  validation/normalization, float-tolerant "sell everything"/"spend all cash"
  (a fractional sell-to-zero used to leave a dust position), and consistent
  valuation across the endpoint, the post-trade snapshot and the 30s task.
- Watchlist: 409 now comes from the repository's `DuplicateTickerError` rather than a
  blanket `except Exception` (real DB errors were being reported as duplicates);
  invalid tickers → 400. Removing a ticker you still hold keeps it streaming, so the
  position can still be valued and sold.
- Bug fixed: watchlist changes made by the LLM never reached the market data source, so
  an AI-added ticker never got a price and could not be traded. `POST /api/chat` now
  reconciles the source with the DB after any action.
- SSE: `create_stream_router` returns a fresh router per call (was mutating a module-level
  one), plus a 15s comment heartbeat on idle connections. Event shape unchanged.
- `app/main.py`: startup snapshot now values positions (was cash-only); startup tracks
  held tickers as well as the watchlist; snapshot interval is a named constant.
- New tests: `tests/routes/test_trading.py`, `tests/routes/test_app.py`,
  `tests/market/test_stream.py`, plus chat/watchlist/portfolio route cases.
  `tests/routes/conftest.py` now uses a real `MarketDataSource` fake.
- Smoke-tested a live uvicorn: health, watchlist, SSE, buy, portfolio, add ticker,
  chat-driven watchlist add, history, and a rejected bad ticker all behave as specified.

In flight: nothing.

Blocked on: nothing.

Interface changes:
- `app/routes/trading.py` is the shared executor. `app/llm/service.py` has adopted it
  (`execute_trade_order` for trades, `value_portfolio` for the prompt's portfolio
  context), so the manual and AI paths share one validation path and one valuation —
  PLAN.md §9 satisfied. The llm-engineer's competing `app/trading.py` proposal was
  withdrawn and deleted.
- The order writes go through the database-engineer's atomic `db.apply_trade`; the
  snapshot stays in the executor because total value needs the price cache.
- No REST/SSE path or payload shape changed.

Final verification on the combined tree: `uv run pytest` 373 passed,
`ruff check app/ tests/` all checks passed.

`tests/routes/test_api_contract.py` pins the response shapes the frontend consumes
(field sets, not values), including that the SSE `timestamp` stays in Unix seconds —
the sparklines key on it. FastAPI `detail` strings are rendered verbatim in the UI,
so treat them as UI copy.

`value_portfolio`'s field names and rounding are now a shared contract: they are both the
GET /api/portfolio response body and the portfolio context the LLM prompt is built from
(the llm-engineer has a prompt test asserting on the formatted P&L string). Treat changes
to it as an interface change requiring an announcement.
