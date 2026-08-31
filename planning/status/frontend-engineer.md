# frontend-engineer — updated 2026-08-31T16:20:00Z
State: done

Done:
- Audited the existing frontend. Baseline was real and working: 31 Jest tests
  passing, static export building. Kept and extended it rather than rewriting.
- `npm test` 100 tests / 11 suites green; `npm run lint` clean; `npm run build`
  produces the static export in `frontend/out/`.
- Verified against the live stack (frontend export served by uvicorn): prices
  stream, flash animations fire, sparklines fill in, trades execute, chat
  round-trips through the real LLM path, chat history survives a reload.
- Added every `data-testid` in `test/e2e/selectors.ts` (see Interface changes).
- New: `lib/treemap.ts` (squarified treemap), `components/Panel.tsx` (shared
  panel chrome so headings survive empty states).
- Fixed: whole-row price flash washed the entire watchlist (every ticker ticks
  each batch) — the class still lands on the row, the animation is scoped to
  the `.flash-target` price cell; price chart showed only the last two points
  (time scale kept the initial one-tick span) — it refits per batch; chat
  history was never loaded from `GET /api/chat/history`; FastAPI errors were
  shown as `400: {"detail":...}` rather than the message.
- Portfolio figures are marked to live SSE prices between the 5s REST polls, so
  header/positions/heatmap move with the stream.

- Heatmap tiles now always render the P&L percentage (they used to drop it
  under 64x40px); the E2E suite reads that text off the tile.
- Re-verified from a clean state after devops reported a stale type error:
  `rm -rf .next out && npm run build` succeeds, 101 Jest tests green, lint clean.

In flight: nothing.

Blocked on: nothing.

Interface changes (frontend-internal, no API contract changed):
- Consumes `GET /api/chat/history` (was previously unused by the UI) and the
  `unrealized_pnl` field of `GET /api/portfolio`. Both already exist.
- Watchlist "Chg%" now shows change since page load (first SSE sample), not the
  tick-over-tick delta, so it matches the sparkline instead of flickering at
  ±0.01%. Falls back to the streamed `change_percent` until two samples exist.
- Watchlist add-input placeholder is "Add symbol" (was "Add ticker") so
  `getByPlaceholder("Ticker")` cannot match both it and the trade-bar input.
  Its `data-testid="watchlist-add-input"` and aria-label are the stable hooks.
- Watchlist remove button glyph is "×" (U+00D7), addressed by
  `data-testid="watchlist-remove-{TICKER}"`.
- Connection status can now read "Reconnecting..." between "Live" and
  "Disconnected".
