# MARKET_DATA.md — The FinAlly Market Data Backend

**One document for everything under `backend/app/market/`.** Start here: it is the summary of
the four documents this module was designed through — `MARKET_DATA_DESIGN.md`,
`MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md` and `MASSIVE_API.md` — which are kept alongside
it as the long-form background (full derivations, the Massive endpoint reference, the
original code listings). Where the two disagree, **this document and the code win**: the
originals were written before the module was built and §13 lists what changed. `PLAN.md`
remains the product specification; this document is how the market half of it is actually
built.

Status as of 2026-08-30: **the market data module and its FastAPI wiring are complete and
tested.** The database, portfolio, watchlist, chat and frontend are not built yet — §12 lists
exactly where they plug in.

```bash
cd backend
uv run pytest -q                                   # 77 tests, ~15 s, no network
uv run python scripts/market_data_demo.py          # watch prices move in the terminal
uv run python scripts/market_data_demo.py --check   # eight assertions, PASS/FAIL
uv run uvicorn app.main:app --port 8000            # then: curl -N localhost:8000/api/stream/prices
```

---

## 1. What this module does

One background task produces prices — from a simulator or from Massive — into an in-memory
cache. The cache derives every tick's `direction`, `change` and `change_pct` and keeps a
600-point ring buffer per ticker. A broadcaster fans coalesced ticks out to every SSE
subscriber every 500 ms.

```
 SimulatedSource ─┐
                  ├─→ MarketDataService ─→ PriceCache ─┬─→ SSE  GET /api/stream/prices
 MassiveSource  ──┘     (producer task)     (ring 600) ├─→      GET /api/history/{ticker}
                                                       ├─→      GET /api/health
                                                       └─→ svc.price(t) → trade fills
```

Everything downstream — the SSE stream, the charts, the trade fill price — is agnostic to
which source is running. That is the entire point of the abstraction.

### Files

| File | Lines | Responsibility |
|---|---|---|
| `app/market/types.py` | 95 | `Quote`, `Tick`, `PricePoint`, `StreamStatus`, `iso_z`, `utcnow` |
| `app/market/seeds.py` | 151 | 57 seed prices + deterministic synthesis for anything else |
| `app/market/source.py` | 62 | The `MarketDataSource` ABC — the whole contract, in one file |
| `app/market/simulator.py` | 193 | `GbmEngine` (pure) + `SimulatedSource` (the wrapper) |
| `app/market/massive.py` | 301 | `MassiveClient` (HTTP) + `MassiveSource` (poll strategy) |
| `app/market/cache.py` | 102 | `PriceCache` — state, tick derivation, ring buffer |
| `app/market/service.py` | 211 | `MarketDataService` — producer, fan-out, failure ladder |
| `app/market/__init__.py` | 72 | `build_source()` — the only place env vars are read |
| `app/routes/market.py` | 84 | SSE, `/api/history/{ticker}`, `/api/health` |
| `app/main.py` | 45 | App + lifespan |
| `app/deps.py` | 16 | `market()` dependency, kept out of `main.py` to avoid a cycle |
| `scripts/market_data_demo.py` | — | Live terminal board + `--check` self-test (§11) |
| `scripts/check_massive.py` | — | Which Massive tier is this key on? |

**Import rule, enforced by `tests/test_module_boundaries.py`:**

```
service → cache → types          simulator ⇄ massive : never
   ├→ simulator → seeds → types  anything → service   : never (from inside the module)
   ├→ massive ─────────→ types
   └→ source ──────────→ types
```

Nothing outside `app/market/` may import anything but `app.market.service` and
`app.market.types` (plus the `build_source` re-export in `__init__`). The test greps for
violations, so this is not an honour system.

---

## 2. The types

| Type | Produced by | Meaning |
|---|---|---|
| `Quote` | a **source** | one observation: `ticker`, `price`, `ts`, optional `session_open` |
| `Tick` | the **cache** | fully derived event; serialises 1:1 to the SSE payload |
| `PricePoint` | the **cache** | one ring-buffer entry: `ts`, `price` |
| `StreamStatus` | the **service** | `connected` / `degraded` / `disconnected` → the header dot |

A source knows a price; it does not know the previous price, so it cannot compute
`direction`. Splitting `Quote` from `Tick` is what makes a source swappable at runtime, and
it means every derived field is computed in exactly one place.

**Timestamps.** `iso_z()` is the only formatter the backend uses:
`2026-08-26T14:03:07.412Z` — UTC, millisecond precision, `Z` suffix. Lexicographic order
equals chronological order, so `ORDER BY recorded_at` is correct without parsing, and the
frontend has exactly one format to handle. `utcnow()` is the module's only clock; tests
patch that one name.

---

## 3. Configuration

Every variable is read **once**, in `build_source()`. The engine, the client and the service
all take plain constructor arguments, so no test ever touches `os.environ`.

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | unset | Set and non-placeholder ⇒ `MassiveSource`; otherwise `SimulatedSource` |
| `SIM_SEED` | unset (OS entropy) | Fixes the RNG. E2E runs set `SIM_SEED=42` |
| `SIM_VOL_SCALE` | `4.0` | Volatility multiplier; `1.0` is statistically honest, `4.0` is watchable |
| `SIM_HALF_LIFE_HOURS` | `4.0` | Mean-reversion half-life; `0` = pure GBM |
| `SIM_INTERVAL_MS` | `500` | Tick cadence **and** `poll_interval` **and** the engine's `dt` |
| `MASSIVE_SNAPSHOT_INTERVAL_S` | `15.0` | SNAPSHOT-mode poll interval |
| `MASSIVE_GROUPED_INTERVAL_S` | `60.0` | GROUPED-mode poll interval (free tier: 1 of 5 calls/min) |
| `MASSIVE_BASE_URL` | `https://api.massive.com` | Override for tests or the legacy Polygon host |

Only `MASSIVE_API_KEY` belongs in `.env.example`. The rest exist for tests and for tuning a
lecture; documenting them to students would imply they need tuning, and they do not.

A key of `""`, `"  "`, or a copied placeholder (`your-...`, `<...>`) selects the **simulator**,
not a wall of 401s. A student who copies `.env.example` and never edits it gets a working app.

---

## 4. The simulator — the default source

Free real data is end-of-day, and outside US market hours even paid data is static. A "real
data" demo can look completely dead. The simulator always produces motion, which is what
makes the terminal worth watching. **Setting `MASSIVE_API_KEY` makes the data real, not
better.**

### The model

Per ticker, per 500 ms tick, in log space:

```
d(log S) = κ(A − log S)·dt  +  (μ − σ²/2)·dt  +  σ·√dt·Z  +  J
             ↑ mean reversion   ↑ drift          ↑ diffusion  ↑ jump
```

- **Time base**: `dt = 0.5 / (252 × 6.5 × 3600)` — a trading year, so μ and σ are the
  annualised numbers a finance person expects.
- **Reversion**: OU pull toward a log anchor `A` that itself drifts with μ, so a trending
  ticker is not yanked back. `SIM_HALF_LIFE_HOURS=4` by default.
- **Jumps**: Poisson, `p = 0.0005` per ticker per tick (~one per ticker per 1,000 s), sized
  2–5% either way — `PLAN.md` §6's "occasional random events". Half of each jump moves the
  anchor, so news permanently re-rates the stock.
- **`SIM_VOL_SCALE = 4.0`**: honest volatility on a 500 ms tick is invisible. 4× is what
  makes a watchlist visibly move without looking absurd.

### Correlation

`Z_i` is a blend of a market factor, a sector factor and an idiosyncratic draw, weighted
0.25 / 0.15 / 0.60 and scaled by the ticker's beta:

```
Z_i = ( √0.25·β̂_i·Z_market + √0.15·Z_sector(i) + √0.60·Z_i,idio ) / n_i
      where β̂_i = β_i / MEAN_BETA   and   n_i = √(0.25·β̂_i² + 0.15 + 0.60)
```

Two decisions here are load-bearing:

- **`MEAN_BETA = 1.08` is a module constant**, the mean over `SEED_PRICES` — *not* the mean
  over the current watchlist. Otherwise a ticker's volatility changes because someone else
  added TSLA: measured, NVDA's realised sd moves 18.8% between watchlists under a
  per-watchlist mean, versus 0.53% with the constant.
- **The `/ n_i` normalisation** keeps `Var(Z_i) = 1` for every beta, so `σ` means exactly
  what the seed table says. Measured realised sd ÷ theoretical: 1.002 / 1.003 / 0.999 for
  AAPL (β 1.05) / NVDA (β 1.55) / KO (β 0.55).

Resulting correlations (jumps and reversion off): AAPL/MSFT 0.377, NVDA/MSFT 0.426,
AAPL/JPM 0.212, AAPL/KO 0.138.

### Seed prices

57 well-known US tickers with plausible mid-2026 levels, drift, volatility, sector and beta.
Recognisability is the goal, not accuracy — a student who sees AAPL near $190 believes the
terminal.

Any symbol outside the table gets a **deterministic** spec from `sha256(symbol)`: price in
$20–$500, σ in 20–60%, μ in −5%…+20%, β in 0.70–1.60, sector `OTHER`. **`sha256`, not the
builtin `hash()`** — `hash()` is salted per process by `PYTHONHASHSEED`, so it would give a
different price on every restart and break E2E reproducibility outright. This is the single
most important line in `seeds.py`.

The table always wins over the hash (AMD is $160.00 from the table, never $104.85 from the
hash), and **an unknown ticker is never rejected**: format validation (`^[A-Z]{1,5}$`)
happens at the API boundary, and by the time `spec_for` is called the symbol is acceptable
by definition. The LLM manages the watchlist proactively, and a rejection mid-conversation
is a worse experience than a plausible synthetic price.

### Determinism — three separate guarantees

1. **Same seed, same path.** `SIM_SEED=42` replays the same prices — RNG consumption order
   is fixed by iterating `sorted(tickers)`, and `step()` on an empty set draws no randomness
   at all.
2. **Same symbol, same seed price, across processes.** `sha256`, not `hash()`.
3. **`prime()` consumes no randomness**, so priming a newly added ticker cannot shift the
   sequence for every other ticker.

Determinism is a claim about the *price path*, not the clock: every `step()` stamps the real
wall clock, so comparisons are made on `(ticker, price, session_open)` fingerprints.

### What it deliberately does not model

Order books, bid/ask spreads, volume, market hours, halts, splits, dividends, after-hours
gaps. `PLAN.md` is market-orders-only with instant fills; none of it would be visible.

---

## 5. Massive (formerly Polygon.io) — the optional real source

### The one fact that shapes the file

A **free key cannot use snapshots** — it gets a `403`, not a `401`, and the difference
matters: 403 means "wrong plan, retrying will never help". A free key is also capped at
**5 calls/min** and serves **end-of-day** data, which rules out per-ticker polling entirely
(10 tickers × `/prev` = 10 calls per round, over budget on the first poll).

So there are two modes, chosen by probing entitlement **once** at startup:

| Mode | Selected when | Endpoint | Interval | Data |
|---|---|---|---|---|
| `SNAPSHOT` | the probe returns 200 (Starter+) | `/v2/snapshot/locale/us/markets/stocks/tickers` | 15 s | live-ish, one call for the whole tracked set |
| `GROUPED` | the probe returns 403 (free tier) | `/v2/aggs/grouped/locale/us/market/stocks/{date}` | 60 s | end-of-day; one call returns every US ticker |

GROUPED mode sets `degraded_reason = "end-of-day data (free Massive tier)"` — the header dot
goes **yellow even though nothing failed**, because the dot is telling the user their prices
are not live, which is true.

### Details that are easy to get wrong

- **Auth via the `Authorization: Bearer` header, never `?apiKey=`.** Request paths get
  logged on failure; a key in a query string ends up in container logs and exception text.
- **Timestamp units are not uniform**: `updated` and `lastTrade.t` are **nanoseconds**;
  `day.t`, `min.t` and grouped `t` are **milliseconds**. Mixing them lands you in the year
  52,000.
- **Grouped quotes are stamped with the bar's own `t`**, not `utcnow()`. Re-polling an
  unchanged EOD bar must produce an identical `Quote` so `PriceCache.apply()` recognises the
  repeat and returns `None`; otherwise the free-tier path emits a `flat` tick every 60 s and
  fills the ring with duplicates.
- **Price fallback chain** per snapshot row: `lastTrade.p → min.c → day.c → prevDay.c`.
  Snapshot data is cleared daily at 3:30 AM ET and repopulates from ~4:00 AM, and in that
  window `day` is zero-filled. This chain is what stops the UI showing $0.00 at 3:45 AM.
- **`todaysChange`/`todaysChangePerc` are not used.** The change column is derived by the
  cache against `session_open`, in one place, for both sources.
- **The trading date is re-resolved once per ET calendar day.** Without it, a container left
  running overnight serves yesterday's close forever. `zoneinfo("America/New_York")`, not a
  fixed −5 offset, so DST is correct.
- **A 429 means the interval is wrong**, not that one request was unlucky:
  `poll_interval × 1.5`, capped at 300 s, and **monotonic** — lowering it on the next success
  oscillates straight back into a 429.
- **Missing tickers are absent, not errors.** v2 has no per-ticker error object; unknown
  symbols simply do not appear. They keep their cached value and get logged at debug.

`scripts/check_massive.py` tells you which tier a key is on, and whether the market is open,
before you debug a frozen UI.

---

## 6. The cache

`PriceCache.apply(quote) -> Tick | None` folds one quote into per-ticker state and returns
the tick to broadcast. `None` means "no new information" — an identical price at or before
the timestamp already held. **Callers must handle `None`**: on a free-tier key every quote
after the first is a repeat.

**Two references, on purpose:**

- `direction` (`up`/`down`/`flat`) compares against `prev_price` — the previous tick. It
  drives the flash animation.
- `change` and `change_pct` compare against `session_open`. They drive the daily-change
  column.

Conflating them makes the change column read ±0.05% forever and look broken. The two
disagree constantly, and that is correct.

`session_open` is pinned to the first price seen unless the source supplies a real one; a
`None` never drifts an established open back.

**The ring buffer** holds the last 600 points per ticker (~5 min at 500 ms) and backs
`GET /api/history/{ticker}`, so charts and sparklines render populated on load instead of
starting from a single point. It is written by the **producer**, not the broadcaster — the
broadcaster coalesces, so feeding it from there would silently drop ticks from history.
`evict()` drops state, ring included, for tickers that leave the tracked set: history is
in-memory only, and re-adding a ticker starts a fresh ring.

---

## 7. The service

`MarketDataService` owns the producer task, the cache, the SSE fan-out and the failure
ladder. It is the only class outside code imports.

### The tracked ticker set

```
tracked = watchlist ∪ {tickers with a non-zero position}
```

Not the watchlist alone. Without the union, removing a held ticker from the watchlist would
freeze its price and silently corrupt the positions table, the heatmap and total portfolio
value. `set_tracked()` is idempotent and must be called after **every** watchlist mutation
**and every trade**.

Newly added tickers are `prime()`d: the simulator returns its seed price immediately (not an
invention — the seed price *is* the opening price), so there is no 500 ms hole where a new
watchlist row shows nothing. `MassiveSource` keeps the default empty `prime()`, because a
real-data source must never invent a price; a new ticker legitimately has no price until the
next poll.

### Fan-out

`subscribe()` returns an `asyncio.Queue` primed with the current cache snapshot, so a client
connecting mid-session renders populated immediately. The broadcaster coalesces pending ticks
into a dict keyed by ticker (a client that fell behind wants the *latest* price, not a
replay) and emits every 500 ms. **A subscriber whose queue is full is dropped**, not awaited
— a wedged client must never apply backpressure to the producer. `EventSource` reconnects on
its own, so the cost of being wrong is one reconnect.

### The failure ladder

| Event | Response |
|---|---|
| `start()` raises (401, DNS, proxy) | log, swap in the simulator, `degraded`. A typo'd key never takes the app down |
| `fetch()` raises, 1st–2nd time | log, exponential backoff with jitter, capped at 60 s |
| `fetch()` raises, 3rd consecutive | swap in the simulator **for the rest of the process**, `degraded` |
| 403 mid-flight (SNAPSHOT) | switch to GROUPED and return `[]` — a strategy change, **not** a failure |
| 429 | raise the interval, re-raise, count it as a failure |
| Healthy source with `degraded_reason` | `degraded` (yellow), no failure counted |
| Any success | failure counter resets to 0 |

The SSE stream must never die because the upstream did. The fallback is terminal and happens
once per process; a simulator that somehow fails is logged, not recursed into.

---

## 8. The wire contract

### `GET /api/stream/prices`

- Price ticks are the **default (unnamed)** event, so `EventSource.onmessage` receives them
  with no extra wiring. Naming them would guarantee a "chart never updates" bug on the
  frontend's first day.
- Stream health is a named `status` event, sent on connect and on every change.
- `: ping` comments every 15 s keep idle connections alive through proxies — necessary
  because the broadcast only emits tickers that *changed*, so a free-tier key can be silent
  for a minute at a time.
- Headers: `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`.

```
data: {"ticker":"AAPL","price":191.24,"prev_price":191.19,"open":190.02,
       "change":1.22,"change_pct":0.64,"direction":"up","ts":"2026-08-26T14:03:07.412Z"}

event: status
data: {"source":"simulator","status":"connected","reason":null,
       "tracked":["AAPL",...],"poll_interval":0.5,"subscribers":1}
```

### `GET /api/history/{ticker}`

`{"ticker": "AAPL", "points": [{"ts": "...", "price": 190.02}, ...]}` — up to 600 points.
**Always 200**, even for an untracked ticker: an empty array is the honest answer and lets
the frontend render a placeholder rather than an error toast. The symbol is trimmed and
upper-cased.

### `GET /api/health`

`{"status": "ok", "market": {...}}`. The outer status is about the *container*: a degraded
market is still a healthy container, so Docker's `HEALTHCHECK` must not flap when the market
data source is having a bad day.

---

## 9. Budgets

| Work | Cost |
|---|---|
| Simulator, 30 tickers | ~90 gauss draws + 30 log/exp per 500 ms tick — microseconds |
| Broadcast | one dict flush + one `put_nowait` per subscriber per 500 ms |
| Ring buffers | 30 tickers × 600 points × ~48 B ≈ 1 MB |
| Free-tier Massive poll | 1 call/min of 5 allowed; ~1–3 MB response filtered to the tracked set |

The SQLite guidance in `PLAN.md` §3 (WAL, busy timeout, `asyncio.to_thread`) does not apply
here yet — this module touches no database at all.

---

## 10. Tests

```bash
cd backend && uv run pytest -q
```

77 tests in ~15 seconds, no network access from any of them (`respx` mocks every Massive
call), covering:

| Area | Highlights |
|---|---|
| `test_source_contract.py` | The same 5 tests run against **both** sources — the interface only earns its keep if both are held to it |
| `test_simulator.py` | Statistical tests at `half_life_hours=0.0` **and** `event_prob=0.0` (both required — with jumps on, sample variance is ~130× the closed form and correlation collapses to 0.004); determinism; the ±25% reversion bound; jumps |
| `test_seeds.py` | All 17,576 three-letter symbols land in range; `SNOW → $348.97` verified in a **subprocess with a different `PYTHONHASHSEED`** — the `hash()`-vs-`sha256` guard |
| `test_cache.py` | First tick flat and open pinned; direction vs prev while change vs open; repeat ⇒ `None`; ring caps at 600; zero open does not divide by zero |
| `test_service.py` | The failure ladder driven by a `FlakySource`; slow-subscriber drop; `prime()` on the simulator but not on Massive |
| `test_massive.py` | 403 ⇒ GROUPED without counting a failure; 401 out of `start()`; 429 raises the interval monotonically; ns/ms timestamps; the 3:30 AM zero-filled window; the key is never in a URL |
| `test_routes.py` | `iso_z` millisecond format; untracked history is 200; the SSE `status`-then-ticks order; the heartbeat; unsubscribe on disconnect |
| `test_module_boundaries.py` | The import graph in §1, enforced by grep |

### Three things the harness gets wrong if you write it the obvious way

1. **`respx` route matching**: with a router built on `base_url`, `url__startswith="/v2/..."`
   matches nothing and every request raises `AllMockedAssertionError`. Use
   `path__startswith`.
2. **SSE cannot be tested through `httpx.ASGITransport`.** It awaits the ASGI app to
   completion and buffers the whole body before returning a response, so an endless stream
   hangs the test run forever with no output. The SSE tests iterate
   `StreamingResponse.body_iterator` directly — the same generator the server pulls from —
   and `aclose()` it to simulate a disconnect.
3. **The failure-ladder tests must pin the backoff jitter.** `_produce` waits
   `min(60, 2**failures) × (0.5 + random())`, i.e. 1–3 s then 2–6 s, so "did it fall back
   within 5 s?" is a coin flip. An autouse fixture patches `random.random` to `0.0`, making
   the delays exactly 1 s and 2 s.

Dependencies are pinned in `backend/uv.lock` — it is committed, and it is what stops a
resolver picking a `respx` whose matcher semantics differ from the ones these tests assume.

---

## 11. Seeing it work: `scripts/market_data_demo.py`

```bash
cd backend
uv run python scripts/market_data_demo.py                    # live board, 30 s
uv run python scripts/market_data_demo.py --seconds 0        # until Ctrl-C
uv run python scripts/market_data_demo.py --check            # self-test, no UI
uv run python scripts/market_data_demo.py --event-prob 0.03  # frequent 2-5% jumps
uv run python scripts/market_data_demo.py --add PYPL         # add a ticker mid-run
```

The board starts a **real `MarketDataService`** and subscribes exactly the way
`GET /api/stream/prices` does, then renders the `Tick` objects the SSE endpoint would
serialise — price, session open, change, change %, a sparkline drawn from the same ring
buffer `/api/history` serves, and the connection dot. If the board moves, the stream moves.
Two glyphs per row on purpose: the one beside the price is `direction` (vs. the previous
tick), the one beside the percentage is the sign against the session open.

Piped or redirected, it logs one line per broadcast frame instead of redrawing.

`--check` runs eight assertions in about ten seconds and prints PASS/FAIL: determinism, seed
sensitivity, table-beats-hash seed prices, unknown-ticker synthesis, that prices actually
move, the closed-form correlations, cache derivation plus `prime()`, and the three-strikes
fallback to the simulator (which takes ~5 s of real backoff to observe).

---

## 12. What is not built yet

| Missing | Where it plugs in |
|---|---|
| Database, watchlist, positions, trades | `app/main.py` pins the tracked set to `DEFAULT_WATCHLIST`. Replace that with `refresh_tracked()` computing `watchlist ∪ held` and call it after every watchlist add/remove, every trade, and on startup |
| Trade execution | `svc.price(ticker)` is the fill price. `None` means "no price yet" ⇒ reject with `400`, naming the ticker |
| Portfolio snapshot task | `PLAN.md` §3 background task 2, every 30 s. This module owns task 1 only |
| LLM chat | Independent of this module; it reaches prices through `MarketDataService` like everything else |
| Frontend, Dockerfile, scripts, E2E | Not started. Static export mounts **after** every `/api/*` route (`PLAN.md` §11) |

The E2E test that matters most to this module is **held-but-unwatched**: buy a ticker, remove
it from the watchlist, assert its price still streams and its position stays live. It is the
union invariant in §7, and the bug it catches is invisible in every other scenario.

---

## 13. Decisions worth not "fixing" back

Each of these looks like a bug at a glance and is not. Most were corrections found while
verifying the design against running code.

| # | Decision | Why |
|---|---|---|
| 1 | The broadcast emits only tickers that **changed**, not all of them every 500 ms | Under the simulator this is identical. Under a free-tier key it is the difference between 60 identical frames a minute and none. The 15 s heartbeat exists because of this |
| 2 | `degraded` covers "working but end-of-day", not only "upstream failed" | The yellow dot means "your prices are not live", which is true on a free key |
| 3 | `MEAN_BETA` is a pinned literal, not computed from `SEED_PRICES` at import | Computing it would silently shift every documented correlation the moment someone adds a ticker to the table. Re-derive it consciously if the beta distribution changes |
| 4 | Grouped quotes carry the **bar's** timestamp, not `utcnow()` | Re-polls must dedupe in the cache, or the free tier spams flat ticks |
| 5 | `prime()` is on the interface, not an `isinstance` check in the service | Removes the "no price" window for a new ticker without the service ever branching on source type |
| 6 | `GbmEngine(event_prob=...)` exists but has no env var | It is a test seam: the statistical tests cannot be written without it. Production behaviour is unchanged |
| 7 | The reversion bound holds only with jumps **off** | Each jump moves the anchor by half, so the anchor itself random-walks (~9.4% sd over 8 h). Measured worst case: 17.7% with jumps off, 52.6% with them on. News that permanently re-rates a stock is exactly what jumps model |
| 8 | Determinism is asserted on price fingerprints, not whole `Quote`s | Every `step()` stamps the wall clock, so two identically seeded engines never share a timestamp |
| 9 | Every synthesised seed field is rounded | Otherwise the top of each range lands one ULP over the bound (`0.20 + 4000/10000 == 0.6000000000000001`) and a range assertion fails on 1 symbol in 4,001 |
| 10 | A `MarketDataService` test double must **not** subclass `SimulatedSource` | `_fall_back` treats a failing simulator as terminal, so a subclass never triggers the swap the test is trying to observe |
| 11 | `MassiveClient.snapshot()` filters the response to the requested symbols | Upstream should only return what was asked for, but a source must never quote a ticker outside the tracked set — the cache would mint state for it and the SSE stream would carry it |
| 12 | The SSE tests drive the route generator, not an HTTP client | `httpx.ASGITransport` buffers; see §10. This is not a shortcut, it is the only way to read an endless stream in-process |
| 13 | The fallback tests take ~3 s each | That is real backoff, pinned to its floor. Making them instant would mean not testing the ladder |

---

## 14. Confidence

**Verified by running code**: everything in §4, §6, §7 — the GBM statistics (sample variance
5.714e-9 against a closed form of 5.732e-9), the correlations, the seed sweep over all 17,576
three-letter symbols, the cache derivation rules, the failure ladder, the SSE route
behaviour, and the demo in §11. `uv run pytest -q` is the current source of truth: 77 passed,
2026-08-30, against the pinned `uv.lock`.

**Not verified against the live service**: no request has ever reached `api.massive.com` from
this repo. The Massive client is exercised entirely against `respx` fixtures built from
documented response shapes. The 403 response *body* shape is reported, not verified — which
is why the client branches on the status code alone. `scripts/check_massive.py` is the first
thing to run with a real key.

**Plausible, not accurate**: the seed prices. They approximate mid-2026 levels because
recognisability is the goal.
