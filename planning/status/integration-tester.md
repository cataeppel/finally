# Integration tester — updated 2026-08-31T00:00Z
State: done

Done:
- Audited the previous E2E pass against PLAN.md §12 and rewrote test/ (see "Suite" below).
- playwright.config.ts: workers=1 (single-user SQLite is shared state), project graph so
  `fresh-start` runs before `app`, trace/video retained on failure, FINALLY_BASE_URL override.
- global-setup.ts: waits for /api/health + a populated price cache; warns on a dirty volume.
- e2e/selectors.ts: every locator prefers `[data-testid]` and falls back to today's markup,
  so frontend testids can land without touching the specs.
- docker-compose.test.yml: builds the app from ../Dockerfile, named db volume, python-based
  healthcheck (base image has no curl), plus a pinned Playwright runner container.
- Agreed LLM_MOCK trigger phrases with llm-engineer; requested testids from frontend-engineer.

- Ran the full suite (33 tests) against a freshly built `finally:test` container on a clean
  `finally-test-data` volume with LLM_MOCK=true: 33/33 pass. Also green twice locally, and
  the non-fresh project passes repeatedly against an already-dirty database (order-independent).

- Verified the containerised runner path too (`docker compose -f test/docker-compose.test.yml
  run --rm playwright`): 33/33 green, exit 0.
- Wrote test/README.md (how to run, why the suite is serial, the mock-LLM contract).

In flight: nothing.

Blocked on: nothing.

Interface changes: none.

## Suite (PLAN.md §12 coverage)
| Scenario (§12) | Spec |
|---|---|
| Fresh start: watchlist, $10k, streaming | e2e/fresh-start.spec.ts |
| Add / remove watchlist ticker | e2e/watchlist.spec.ts |
| Buy: cash down, position appears | e2e/trading.spec.ts |
| Sell: cash up, position reduces / disappears | e2e/trading.spec.ts |
| Heatmap colours, P&L chart data points | e2e/portfolio.spec.ts |
| AI chat mocked, inline trade execution | e2e/chat.spec.ts |
| SSE disconnect + reconnect | e2e/sse-resilience.spec.ts |

Also covered beyond §12: flash animation class, progressive sparklines, ticker click selects
the chart, insufficient cash / insufficient shares rejection, portfolio total = cash + positions,
positions table vs API, chat history persistence, SSE payload shape.

## Outstanding defects
None. No application defect has been found: every failure in the first full run traced back
to stale assertions in my own specs (the frontend refactor changed inline chat wording, added
a neutral grey heatmap state at exactly 0% P&L, and introduced testids that made a couple of
`.or()` locators ambiguous). All fixed test-side and re-verified.

Notes for other agents, none of them blocking:
- Chromium's offline emulation does not tear down an already-open EventSource, so the
  disconnect test severs the stream at the network-interception layer instead.
- The SELL button overlap that forced a JS click in the previous E2E pass is gone; the suite
  now clicks it normally.
