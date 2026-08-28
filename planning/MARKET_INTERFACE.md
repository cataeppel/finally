# MARKET_INTERFACE.md — The Unified Market Data Interface

**Status:** design contract for the Market Data agent. Binding on `backend/app/market/`.
**Companions:** `MASSIVE_API.md` (upstream REST details), `MARKET_SIMULATOR.md` (the GBM engine).

Everything above the price cache — SSE streaming, `/api/history`, portfolio valuation, the
LLM's portfolio context — must be unable to tell whether prices came from Massive or from
the simulator. This document defines the seam that makes that true.

---

## 1. Module layout

```
backend/app/market/
├── __init__.py      # public surface: get_market_service()
├── types.py         # Quote, Tick, PricePoint, TickerState, StreamStatus
├── source.py        # MarketDataSource — the abstract interface
├── seeds.py         # seed price table + deterministic unknown-ticker pricing
├── simulator.py     # SimulatedSource  (implements MarketDataSource)
├── massive.py       # MassiveSource    (implements MarketDataSource)
├── cache.py         # PriceCache       — state, ring buffers, tick derivation
└── service.py       # MarketDataService — the one thing the rest of the app touches
```

Import direction is strictly one-way: `service → {cache, source, simulator, massive} → types`.
`simulator.py` and `massive.py` never import each other and never import `service.py`.
Nothing outside `backend/app/market/` imports anything but `service` and `types`.

---

## 2. Types

```python
# backend/app/market/types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class StreamStatus(str, Enum):
    """Drives the connection dot in the header (PLAN.md §10)."""
    CONNECTED = "connected"        # green  — live source, healthy
    DEGRADED = "degraded"          # yellow — fell back to the simulator, or EOD-only data
    DISCONNECTED = "disconnected"  # red    — no producer running


@dataclass(frozen=True, slots=True)
class Quote:
    """One observation from a source. The ONLY thing a source ever produces.

    `session_open` is the source's own reference for the daily change column.
    Massive supplies the real session open (`day.o`); the simulator supplies the
    first price it minted this process. `None` means "no opinion" — the cache
    then pins the session open to the first price it sees.
    """
    ticker: str
    price: float
    ts: datetime                    # timezone-aware UTC
    session_open: float | None = None


@dataclass(frozen=True, slots=True)
class Tick:
    """A fully derived price event. Serialises 1:1 to the SSE payload in PLAN.md §6."""
    ticker: str
    price: float
    prev_price: float
    open: float
    change: float                   # price - open
    change_pct: float               # 100 * change / open
    direction: str                  # "up" | "down" | "flat", vs. prev_price
    ts: datetime

    def to_payload(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 4),
            "prev_price": round(self.prev_price, 4),
            "open": round(self.open, 4),
            "change": round(self.change, 4),
            "change_pct": round(self.change_pct, 4),
            "direction": self.direction,
            "ts": iso_z(self.ts),
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One entry in a ring buffer. Backs GET /api/history/{ticker}."""
    ts: datetime
    price: float


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601, millisecond precision, 'Z' suffix — PLAN.md §7 conventions.

    One formatter, used by every timestamp the backend emits, so lexicographic
    order always equals chronological order.
    """
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
```

**`Quote` in, `Tick` out.** A source knows a price; it does not know the previous price, so
it cannot compute `direction`. Only the cache has the history needed to derive a `Tick`.
Keeping that split means a source is a pure function of the upstream, and every derived
field is computed in exactly one place.

---

## 3. The source interface

```python
# backend/app/market/source.py
from __future__ import annotations

import abc

from .types import Quote


class MarketDataSource(abc.ABC):
    """A pollable source of prices. Implemented by SimulatedSource and MassiveSource.

    Contract:
      * `fetch()` performs ONE round of work and returns whatever it learned.
      * `fetch()` may raise. The caller (MarketDataService) owns retries, backoff,
        and fallback — an implementation must not sleep, retry, or swallow errors.
      * `fetch()` may return fewer quotes than there are tracked tickers, or none.
        A missing ticker means "no news", not "price is zero".
      * All methods are called from a single asyncio task. No internal locking needed.
    """

    #: stable identifier surfaced in /api/health and logs: "simulator" | "massive"
    name: str

    #: seconds between fetch() calls. May be read after each fetch, so an
    #: implementation is allowed to raise its own interval (e.g. after a 429).
    poll_interval: float

    #: True when this source cannot deliver moving prices (EOD data) and the
    #: stream should be marked degraded even though nothing has failed.
    degraded_reason: str | None = None

    async def start(self) -> None:
        """Open connections, resolve entitlement, backfill. May raise."""

    async def aclose(self) -> None:
        """Release resources. Must be idempotent and must not raise."""

    @abc.abstractmethod
    def set_tickers(self, tickers: frozenset[str]) -> None:
        """Replace the tracked set. Called on every watchlist/position change.

        Cheap and synchronous — no I/O. A source that needs to do work for a new
        ticker does it lazily inside the next fetch().
        """

    @abc.abstractmethod
    async def fetch(self) -> list[Quote]:
        """One poll round."""
```

### Why `fetch()` and not an async generator

The obvious alternative is `async def stream(self) -> AsyncIterator[Quote]`, with each
source owning its own loop. We deliberately do not do that:

- **Retry policy lives in one place.** PLAN.md §6 specifies exponential backoff capped at
  60 s and fallback to the simulator after three consecutive failures. With `fetch()`, that
  policy is ~20 lines in `service.py` and applies to every source ever written. With
  generators, each source reimplements it — and the simulator's copy would be dead code
  that nobody tests.
- **Tests need no clock.** `await source.fetch()` returns a list. Asserting on GBM output
  or on snapshot parsing requires no event loop tricks, no `anext()`, no cancellation.
- **The fallback swap is trivial.** Replacing a failed source is one assignment between
  polls. Interrupting a half-consumed generator is not.

---

## 4. Selecting a source

```python
# backend/app/market/__init__.py
import os

from .massive import MassiveSource
from .simulator import SimulatedSource
from .source import MarketDataSource


def build_source() -> MarketDataSource:
    """Env-var driven selection — PLAN.md §5.

    Set and non-empty after stripping -> Massive. Anything else -> simulator.
    A key of "" or "  " or the literal placeholder from .env.example must NOT
    send us down the Massive path; a student who copies .env.example and never
    edits it should get the simulator, not a wall of 401s.
    """
    key = (os.getenv("MASSIVE_API_KEY") or "").strip()
    if not key or key.startswith("your-"):
        return SimulatedSource()
    return MassiveSource(api_key=key)
```

`SimulatedSource` takes no configuration in production. Tests inject a seed
(`SimulatedSource(seed=1234)`) for reproducibility — see `MARKET_SIMULATOR.md` §7.

---

## 5. The price cache

Owns all per-ticker state and is the only place a `Tick` is created.

```python
# backend/app/market/cache.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .types import PricePoint, Quote, Tick

RING_SIZE = 600     # PLAN.md §6: ~5 minutes at the simulator's 500 ms cadence


@dataclass(slots=True)
class TickerState:
    ticker: str
    price: float
    prev_price: float
    session_open: float
    updated_at: datetime
    ring: deque[PricePoint] = field(default_factory=lambda: deque(maxlen=RING_SIZE))


class PriceCache:
    """In-memory, single-process, never persisted. Rebuilt from scratch on restart."""

    def __init__(self) -> None:
        self._states: dict[str, TickerState] = {}

    def apply(self, quote: Quote) -> Tick | None:
        """Fold one quote into state and return the Tick to broadcast.

        Returns None when the quote carries no new information (identical price
        at or before the timestamp we already hold). Callers must handle None —
        on a free-tier Massive key EVERY quote after the first is a repeat.
        """
        st = self._states.get(quote.ticker)
        if st is None:
            open_ = quote.session_open if quote.session_open is not None else quote.price
            st = TickerState(
                ticker=quote.ticker,
                price=quote.price,
                prev_price=quote.price,      # first tick is flat, by definition
                session_open=open_,
                updated_at=quote.ts,
            )
            self._states[quote.ticker] = st
        else:
            if quote.price == st.price and quote.ts <= st.updated_at:
                return None
            st.prev_price = st.price
            st.price = quote.price
            st.updated_at = quote.ts
            # A source may learn the real session open late (Massive backfill);
            # never let it drift once set from a real value.
            if quote.session_open is not None:
                st.session_open = quote.session_open

        st.ring.append(PricePoint(ts=quote.ts, price=quote.price))
        return _derive_tick(st)

    def get(self, ticker: str) -> TickerState | None:
        return self._states.get(ticker)

    def price(self, ticker: str) -> float | None:
        st = self._states.get(ticker)
        return st.price if st else None

    def snapshot(self) -> dict[str, Tick]:
        """Every tracked ticker's current tick — used to prime a new SSE subscriber."""
        return {t: _derive_tick(st) for t, st in self._states.items()}

    def history(self, ticker: str) -> list[PricePoint]:
        st = self._states.get(ticker)
        return list(st.ring) if st else []

    def evict(self, keep: frozenset[str]) -> None:
        """Drop state for tickers that left the tracked set."""
        for t in list(self._states):
            if t not in keep:
                del self._states[t]


def _derive_tick(st: TickerState) -> Tick:
    open_ = st.session_open or st.price          # never divide by zero
    change = st.price - open_
    if st.price > st.prev_price:
        direction = "up"
    elif st.price < st.prev_price:
        direction = "down"
    else:
        direction = "flat"
    return Tick(
        ticker=st.ticker,
        price=st.price,
        prev_price=st.prev_price,
        open=open_,
        change=change,
        change_pct=(change / open_ * 100.0) if open_ else 0.0,
        direction=direction,
        ts=st.updated_at,
    )
```

**Two references, on purpose** (PLAN.md §6): `direction` compares to `prev_price` (the
previous tick, drives the flash); `change`/`change_pct` compare to `session_open` (drives
the daily-change column). Conflating them pins the change column near ±0.00% forever.

**The ring buffer is fed by the producer, not the broadcaster.** One entry per *actual
price change*, not one per 500 ms SSE frame. So 600 points means ~5 minutes of simulator
history, and ~2.5 hours of Massive history at a 15 s poll — both useful chart seeds, and
neither is 600 copies of the same number.

---

## 6. `MarketDataService` — the public surface

The only object the rest of the backend touches.

```python
# backend/app/market/service.py  (abridged — policy is the point, not the plumbing)
from __future__ import annotations

import asyncio
import logging
import random

from .cache import PriceCache
from .simulator import SimulatedSource
from .source import MarketDataSource
from .types import PricePoint, StreamStatus, Tick

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3     # PLAN.md §6
BACKOFF_CAP_SECONDS = 60.0
BROADCAST_INTERVAL = 0.5         # PLAN.md §6 — SSE cadence


class MarketDataService:
    def __init__(self, source: MarketDataSource) -> None:
        self._source = source
        self._cache = PriceCache()
        self._tracked: frozenset[str] = frozenset()
        self._subscribers: set[asyncio.Queue[list[Tick]]] = set()
        self._pending: dict[str, Tick] = {}      # coalesced since last broadcast
        self._status = StreamStatus.DISCONNECTED
        self._degraded_reason: str | None = None
        self._tasks: list[asyncio.Task] = []

    # ---- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        await self._source.start()
        self._apply_source_health()
        self._tasks = [
            asyncio.create_task(self._produce(), name="market-producer"),
            asyncio.create_task(self._broadcast(), name="market-broadcast"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._source.aclose()
        self._status = StreamStatus.DISCONNECTED

    # ---- reads --------------------------------------------------------

    def price(self, ticker: str) -> float | None:
        """Fill price for a trade. None means 'no price yet' -> reject with 400."""
        return self._cache.price(ticker)

    def snapshot(self) -> dict[str, Tick]:
        return self._cache.snapshot()

    def history(self, ticker: str) -> list[PricePoint]:
        return self._cache.history(ticker)

    @property
    def status(self) -> StreamStatus:
        return self._status

    @property
    def health(self) -> dict:
        """Surfaced by GET /api/health and on the SSE 'status' event."""
        return {
            "source": self._source.name,
            "status": self._status.value,
            "reason": self._degraded_reason,
            "tracked": sorted(self._tracked),
            "poll_interval": self._source.poll_interval,
        }

    # ---- tracked set ---------------------------------------------------

    def set_tracked(self, tickers: frozenset[str]) -> None:
        """watchlist ∪ {tickers with a non-zero position} — PLAN.md §6.

        Idempotent and safe to call on every mutation. Must be called after every
        watchlist add/remove AND after every trade, or a held-but-unwatched ticker
        freezes and the positions table silently goes stale.
        """
        if tickers == self._tracked:
            return
        self._tracked = tickers
        self._source.set_tickers(tickers)
        self._cache.evict(tickers)

    # ---- SSE fan-out ---------------------------------------------------

    def subscribe(self) -> asyncio.Queue[list[Tick]]:
        q: asyncio.Queue[list[Tick]] = asyncio.Queue(maxsize=64)
        snap = list(self._cache.snapshot().values())
        if snap:
            q.put_nowait(snap)      # a new client renders populated immediately
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- the two background tasks --------------------------------------

    async def _produce(self) -> None:
        """PLAN.md §3, background task 1. Poll, fold into cache, apply failure policy."""
        failures = 0
        while True:
            try:
                for quote in await self._source.fetch():
                    tick = self._cache.apply(quote)
                    if tick is not None:
                        self._pending[tick.ticker] = tick
                failures = 0
                self._apply_source_health()
                delay = self._source.poll_interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:                       # noqa: BLE001 — nothing may kill this loop
                failures += 1
                log.warning("market: %s fetch failed (%d/%d): %s",
                            self._source.name, failures, MAX_CONSECUTIVE_FAILURES, exc)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    await self._fall_back(str(exc))
                    failures = 0
                    delay = self._source.poll_interval
                else:
                    # exponential backoff with jitter, capped at 60 s
                    delay = min(BACKOFF_CAP_SECONDS, 2.0 ** failures) * (0.5 + random.random())
            await asyncio.sleep(delay)

    async def _broadcast(self) -> None:
        """PLAN.md §6. Emit coalesced ticks to every subscriber at a fixed cadence."""
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL)
            if not self._pending:
                continue
            batch, self._pending = list(self._pending.values()), {}
            for q in list(self._subscribers):
                try:
                    q.put_nowait(batch)
                except asyncio.QueueFull:
                    # A wedged client must never apply backpressure to the producer.
                    self._subscribers.discard(q)
                    log.info("market: dropped a slow SSE subscriber")

    # ---- degradation ----------------------------------------------------

    def _apply_source_health(self) -> None:
        reason = self._source.degraded_reason
        self._degraded_reason = reason
        self._status = StreamStatus.DEGRADED if reason else StreamStatus.CONNECTED

    async def _fall_back(self, reason: str) -> None:
        """Terminal, once per process — PLAN.md §6."""
        if isinstance(self._source, SimulatedSource):
            log.error("market: simulator itself failed: %s", reason)
            return
        log.error("market: %s failed %d times, falling back to the simulator: %s",
                  self._source.name, MAX_CONSECUTIVE_FAILURES, reason)
        old, self._source = self._source, SimulatedSource()
        await old.aclose()
        await self._source.start()
        self._source.set_tickers(self._tracked)
        self._status = StreamStatus.DEGRADED
        self._degraded_reason = f"upstream unavailable, using simulator ({reason})"
```

### Failure ladder, in one place

| Situation | Reaction | Status |
|---|---|---|
| `fetch()` raises, attempt 1–2 | backoff `2^n × jitter`, cap 60 s, keep cached prices | `connected` |
| `fetch()` raises, attempt 3 | **swap in `SimulatedSource`, permanently** | `degraded` |
| Massive 401 (bad key) | `MassiveSource.start()` raises → build_source's caller falls back at startup | `degraded` |
| Massive 403 (free tier) | handled **inside** `MassiveSource`: switch SNAPSHOT → GROUPED, do not count as a failure | `degraded` (EOD data) |
| Massive 429 | counts as a failure **and** the source raises its own `poll_interval` | `connected` until 3 strikes |
| Source is healthy but EOD-only | `degraded_reason` set, no failure counted | `degraded` |

Yellow means *"reconnecting **or** degraded"* per PLAN.md §10 — a free-tier Massive key
that is working perfectly still shows yellow, because the prices genuinely are not live.
`health.reason` is the string the frontend shows on hover.

---

## 7. Wiring into FastAPI

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .market import build_source
from .market.service import MarketDataService


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = MarketDataService(build_source())
    app.state.market = service
    await service.start()
    service.set_tracked(await tracked_tickers())   # watchlist ∪ held positions
    snapshots = asyncio.create_task(snapshot_loop(app))   # background task 2, PLAN.md §3
    try:
        yield
    finally:
        snapshots.cancel()
        await service.stop()


def market(request: Request) -> MarketDataService:
    return request.app.state.market            # FastAPI dependency
```

Route handlers use it directly:

```python
@router.get("/api/history/{ticker}")
async def history(ticker: str, svc: MarketDataService = Depends(market)):
    return {"ticker": ticker,
            "points": [{"ts": iso_z(p.ts), "price": p.price} for p in svc.history(ticker)]}


@router.post("/api/portfolio/trade")
async def trade(req: TradeRequest, svc: MarketDataService = Depends(market)):
    price = svc.price(req.ticker)
    if price is None:
        raise HTTPException(400, f"No price available for {req.ticker} yet — try again in a moment")
    ...
```

### The tracked-set invariant

`set_tracked()` must be called after **every** mutation of either input:

| Trigger | Why |
|---|---|
| `POST /api/watchlist` | new ticker needs pricing before its row can render |
| `DELETE /api/watchlist/{t}` | shrinks the set — *unless* a position is still held |
| `POST /api/portfolio/trade` | a buy can introduce a ticker that was never watched |
| `POST /api/portfolio/reset` | back to the default ten |
| LLM auto-executed trades / watchlist changes | same paths, same requirement |

Put it behind one helper (`await refresh_tracked(app)`) that recomputes
`watchlist ∪ held` from the database and calls `set_tracked`, then call *that* from every
site above. Do not let callers assemble the union themselves — PLAN.md §6 is explicit that
missing the union corrupts the positions table, and the bug is invisible until someone
removes a held ticker from the watchlist.

### Newly added tickers

`set_tracked()` does no I/O, so a brand-new ticker has **no price until the next poll**:
instant for the simulator, up to 15 s (SNAPSHOT) or 60 s (GROUPED) for Massive. Until then
`svc.price()` returns `None`, `/api/history` returns an empty list, and the watchlist row
must render a placeholder (`—`) rather than `$0.00`. Trades against a priceless ticker are
rejected with a `400` explaining the wait. This is a small, honest gap; do not paper over
it by minting a fake price for a real-data source.

---

## 8. Implementing the interface

### `SimulatedSource`

```python
class SimulatedSource(MarketDataSource):
    name = "simulator"

    def __init__(self, *, seed: int | None = None, interval: float = 0.5) -> None:
        self.poll_interval = interval
        self.degraded_reason = None            # the simulator is never degraded
        self._engine = GbmEngine(seed=seed)    # see MARKET_SIMULATOR.md
        self._tickers: frozenset[str] = frozenset()

    def set_tickers(self, tickers: frozenset[str]) -> None:
        self._engine.ensure(tickers)           # mints seed prices for new symbols
        self._tickers = tickers

    async def fetch(self) -> list[Quote]:
        return self._engine.step(self._tickers)   # pure CPU, no await, never raises
```

`fetch()` is synchronous work behind an `async def`. That is correct: one GBM step over
≤30 tickers is microseconds. Do **not** offload it to a thread — the overhead exceeds the
work, and `MARKET_SIMULATOR.md` §6 depends on single-threaded RNG ordering for determinism.

### `MassiveSource`

```python
class MassiveSource(MarketDataSource):
    name = "massive"

    def __init__(self, api_key: str) -> None:
        self._client = MassiveClient(api_key)       # see MASSIVE_API.md §7
        self._mode: str | None = None               # "snapshot" | "grouped"
        self._date: str | None = None               # cached trading date, GROUPED mode
        self._tickers: frozenset[str] = frozenset()
        self.poll_interval = 15.0
        self.degraded_reason = None

    def set_tickers(self, tickers: frozenset[str]) -> None:
        self._tickers = tickers                     # no I/O; used by the next fetch()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def start(self) -> None:
        """Probe entitlement once. 403 -> free tier -> GROUPED mode."""
        try:
            await self._client.snapshot({"AAPL"})
            self._mode, self.poll_interval = "snapshot", 15.0
        except MassiveEntitlementError:
            self._mode, self.poll_interval = "grouped", 60.0
            self._date = await self._client.latest_trading_date()
            self.degraded_reason = "end-of-day data (free Massive tier)"
            log.warning("market: free-tier key — end-of-day prices, poll every 60s")

    async def fetch(self) -> list[Quote]:
        if self._mode == "snapshot":
            return await self._client.snapshot(set(self._tickers))
        return await self._client.grouped_daily(self._date, set(self._tickers))
```

A `401` from `start()` propagates; the caller catches it, logs "bad MASSIVE_API_KEY", and
constructs a `SimulatedSource` instead. Never let a typo'd key take the app down.

---

## 9. Test contract

The interface earns its keep only if both implementations are held to the same suite.

```python
# backend/tests/test_market_source.py
@pytest.fixture(params=["simulator", "massive"])
def source(request, respx_mock) -> MarketDataSource:
    if request.param == "simulator":
        return SimulatedSource(seed=42)
    respx_mock.get(url__startswith="https://api.massive.com/v2/snapshot").respond(
        json=load_fixture("snapshot_aapl_msft.json"))
    return MassiveSource(api_key="test-key")


async def test_fetch_returns_quotes_only_for_tracked_tickers(source):
    await source.start()
    source.set_tickers(frozenset({"AAPL", "MSFT"}))
    quotes = await source.fetch()
    assert {q.ticker for q in quotes} <= {"AAPL", "MSFT"}
    assert all(q.price > 0 and q.ts.tzinfo is not None for q in quotes)


async def test_set_tickers_is_idempotent_and_io_free(source): ...
async def test_aclose_is_idempotent(source): ...
async def test_empty_ticker_set_yields_no_quotes(source): ...
```

Plus targeted tests that need only one side:

| Test | Target |
|---|---|
| 403 on the entitlement probe selects GROUPED mode and sets `degraded_reason` | `MassiveSource` |
| 401 propagates out of `start()` | `MassiveSource` |
| nanosecond `updated` and millisecond `day.t` both parse to sane datetimes | `massive._parse_snapshot_row` |
| zero-filled `day` (the 3:30–4:00 AM ET window) falls through to `prevDay.c` | `massive._parse_snapshot_row` |
| same seed ⇒ identical price sequence | `SimulatedSource` |
| unknown ticker gets a deterministic price in $20–$500 | `seeds` |
| three consecutive `fetch()` raises ⇒ source becomes `SimulatedSource`, status `degraded` | `MarketDataService` |
| a repeated identical quote returns `None` from `PriceCache.apply` | `PriceCache` |
| `change_pct` is measured against `session_open`, `direction` against `prev_price` | `PriceCache` |
| ring buffer caps at 600 and drops oldest first | `PriceCache` |
| a full `QueueFull` subscriber is dropped without stalling the producer | `MarketDataService` |

Use `respx` to mock `httpx` — never hit `api.massive.com` from a test.

---

## 10. Deliberate deviations from PLAN.md

Two, both narrow, both recorded here so no one "fixes" them back:

1. **The SSE broadcast emits only tickers that changed**, rather than every tracked ticker
   every 500 ms. Under the simulator this is identical — everything changes every tick.
   Under a free-tier Massive key it is the difference between 60 identical events per
   second and none. The SSE endpoint must therefore send a comment heartbeat (`: ping\n\n`)
   every 15 s so idle connections survive proxies, and `EventSource`'s own reconnect
   handles the rest.

2. **`degraded` covers "working but end-of-day", not just "upstream failed".** PLAN.md §10
   defines yellow as "reconnecting *or* degraded"; this extends the second case to a
   healthy free-tier key. It is the correct signal: the dot is telling the user their
   prices are not live, which is true.
