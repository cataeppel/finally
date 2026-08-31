# FinAlly E2E tests

Playwright end-to-end tests covering every scenario in `planning/PLAN.md` §12.

## Running

Against a container (the definition-of-done run), from the repository root:

```bash
docker compose -f test/docker-compose.test.yml down -v      # clean database volume
docker compose -f test/docker-compose.test.yml up -d --build app
cd test && npx playwright test                              # or: npm run docker:test
```

`npm run docker:test` runs the same suite inside the pinned Playwright container instead of
using a local browser install. Point the suite at a different host with `FINALLY_BASE_URL`.

The app must run with `LLM_MOCK=true`; the compose file sets it.

## Why the suite is serial and ordered

FinAlly is single-user, so one SQLite database backs the whole run:

- `workers: 1` — parallel workers would trade against each other's state.
- Two Playwright projects: `fresh-start` asserts the pristine seed ($10,000, ten default
  tickers, no positions) and runs first; `app` depends on it and only makes relative
  assertions, so it passes against a dirty database too.
- **Always start from a clean volume** (`down -v`). `global-setup.ts` warns loudly if the
  database is not freshly seeded rather than letting it look like a product bug.

## Layout

| File | Purpose |
|---|---|
| `e2e/fresh-start.spec.ts` | Seeded state, streaming prices, flash animation, sparklines |
| `e2e/watchlist.spec.ts` | Add / remove / duplicate ticker, chart selection |
| `e2e/trading.spec.ts` | Buy, partial sell, full sell, insufficient cash / shares |
| `e2e/portfolio.spec.ts` | Heatmap colours and sizing, P&L chart, positions table |
| `e2e/chat.spec.ts` | Mocked LLM chat, inline trade and watchlist confirmations |
| `e2e/sse-resilience.spec.ts` | Stream drop, reconnection, event payload shape |
| `e2e/selectors.ts` | Every locator, each preferring `data-testid` over markup structure |
| `e2e/helpers.ts` | API setup helpers and value parsing |
| `global-setup.ts` | Waits for `/api/health` and a warm price cache |

Add new locators to `e2e/selectors.ts` rather than reaching into the DOM from a spec — that
keeps frontend markup changes to a single file.

## Mock LLM contract

`chat.spec.ts` depends on the deterministic replies in `backend/app/llm/mock.py`: `hello`,
`how is my portfolio doing?`, `buy N TICKER`, `sell N TICKER`, `add TICKER to watchlist`.
Changing those replies breaks the chat spec.
