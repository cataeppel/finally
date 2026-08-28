# MARKET_DATA_DESIGN.md — Implementation Design for the Market Data Backend

**Status:** implementation specification. Binding on `backend/app/market/` and on the
FastAPI wiring that consumes it.
**Audience:** the agent that writes the code. This document is meant to be followed
top-to-bottom; every file in the module is given in full.

**Relationship to the other planning documents**

| Document | Role | This document's relationship |
|---|---|---|
| `PLAN.md` | Product & system spec | Source of truth. Every requirement here traces to a `PLAN.md` section. |
| `MARKET_INTERFACE.md` | Design contract for the seam | Fixes the module layout, the `MarketDataSource` ABC, the cache semantics, the failure ladder. **Not restated — implemented.** |
| `MARKET_SIMULATOR.md` | Design contract for the GBM engine | Fixes the model, calibration, correlation structure, determinism rules. |
| `MASSIVE_API.md` | Upstream research reference | Fixes endpoints, entitlements, response shapes, timestamp units. |

Where those three documents give an abridged sketch, this one gives the code that ships.
Where they conflict or contain an error, §16 records the resolution — **nothing is
silently changed.**

---

## 1. Scope

The market data backend owns exactly one thing: **turning some upstream into a stream of
derived price ticks that the rest of FinAlly can consume without knowing what the upstream
was.**

**In scope**

- The unified `MarketDataSource` interface and its two implementations
- The GBM simulator (default source, `PLAN.md` §6)
- The Massive REST client (optional source, `PLAN.md` §6)
- The in-memory price cache: session open, previous price, ring buffers
- Tick derivation (`direction`, `change`, `change_pct`)
- SSE fan-out to browser clients, with coalescing, heartbeats and slow-client eviction
- The failure ladder: backoff, entitlement downgrade, permanent fallback, `degraded` status
- `GET /api/stream/prices`, `GET /api/history/{ticker}`, and the market half of `GET /api/health`

**Out of scope** (owned by other agents, consuming this module through
`MarketDataService` only)

- The database, trades, positions, portfolio valuation
- The LLM chat flow
- The portfolio snapshot background task (`PLAN.md` §3, task 2)
- Every frontend concern except the wire format in §14

**The one-sentence contract for everyone else:** *ask `MarketDataService` for a price, a
history, a snapshot or the health dict; tell it when the tracked set changes; never import
anything else from `app.market`.*

---

## 2. Module map and import rules

```
backend/app/market/
├── __init__.py      build_source() + config reading           §12
├── types.py         Quote, Tick, PricePoint, StreamStatus, iso_z   §4
├── source.py        MarketDataSource ABC                      §6
├── seeds.py         seed table + deterministic synthesis      §5
├── simulator.py     GbmEngine + SimulatedSource               §7
├── massive.py       MassiveClient + MassiveSource             §8
├── cache.py         PriceCache + TickerState                  §9
└── service.py       MarketDataService                         §10
```

Import direction is strictly one-way and enforced by review:

```
service ──► cache ──► types
   │          ▲
   ├──► simulator ──► seeds ──► types
   ├──► massive ──────────────► types
   └──► source ───────────────► types
```

- `simulator.py` and `massive.py` never import each other and never import `service.py`.
- `service.py` imports `SimulatedSource` — that is deliberate and unavoidable: the fallback
  target is hard-coded policy (`PLAN.md` §6), not configuration.
- Nothing outside `backend/app/market/` imports anything except `service` and `types`.

A cheap guard worth having in the test suite:

```python
# backend/tests/test_module_boundaries.py
import pathlib, re

MARKET = pathlib.Path(__file__).parents[1] / "app" / "market"

def test_sources_do_not_import_each_other():
    sim = (MARKET / "simulator.py").read_text()
    mas = (MARKET / "massive.py").read_text()
    assert "massive" not in sim and "simulator" not in mas
    for f in ("simulator.py", "massive.py", "cache.py", "source.py"):
        assert "from .service" not in (MARKET / f).read_text()

def test_app_only_imports_the_public_surface():
    for path in (MARKET.parents[0]).rglob("*.py"):
        if MARKET in path.parents or path == MARKET:
            continue
        for mod in re.findall(r"from \.+market\.(\w+)", path.read_text()):
            assert mod in {"service", "types"}, f"{path} reaches into app.market.{mod}"
```

---

## 3. Configuration

Every variable is read **once**, in `build_source()` (§12). The engine, the client and the
service all take plain constructor arguments so no test ever touches `os.environ`.

| Variable | Default | Read where | Effect |
|---|---|---|---|
| `MASSIVE_API_KEY` | unset | `build_source` | Set and non-placeholder ⇒ `MassiveSource`; otherwise `SimulatedSource` (`PLAN.md` §5) |
| `SIM_SEED` | unset (OS entropy) | `build_source` | Fixes the RNG. E2E runs set `SIM_SEED=42` |
| `SIM_VOL_SCALE` | `4.0` | `build_source` | Volatility multiplier; `1.0` = statistically honest (`MARKET_SIMULATOR.md` §2) |
| `SIM_HALF_LIFE_HOURS` | `4.0` | `build_source` | OU half-life; `0` = pure GBM |
| `SIM_INTERVAL_MS` | `500` | `build_source` | Tick cadence **and** `poll_interval` **and** the engine's `dt` |
| `MASSIVE_SNAPSHOT_INTERVAL_S` | `15.0` | `build_source` | SNAPSHOT-mode poll interval |
| `MASSIVE_GROUPED_INTERVAL_S` | `60.0` | `build_source` | GROUPED-mode poll interval (free tier: 1 of 5 calls/min) |
| `MASSIVE_BASE_URL` | `https://api.massive.com` | `build_source` | Override for tests / the legacy `api.polygon.io` host |
| `MASSIVE_BACKFILL` | `false` | `build_source` | Optional one-shot minute-bar chart backfill (§8.6) |

Only `MASSIVE_API_KEY` belongs in `.env.example`. The `SIM_*` and `MASSIVE_*_INTERVAL_S`
knobs exist for tests and for tuning a lecture; documenting them to students would imply
they need tuning, and they do not.

**`SIM_INTERVAL_MS` drives three things that must stay consistent**: the source's
`poll_interval`, the engine's `dt_seconds`, and — indirectly — how much wall-clock time
600 ring-buffer entries represent. Pass it once from `build_source()` and derive the rest;
never hard-code `0.5` in two places.

---

## 4. `types.py`

```python
# backend/app/market/types.py
"""Value types shared by every part of the market module.

`Quote` is what a SOURCE produces. `Tick` is what the CACHE derives and what the
SSE stream emits. Keeping them separate is the whole reason a source can be swapped
at runtime: a source knows a price, it does not know the previous price, so it
cannot compute `direction`. Every derived field is computed in exactly one place.
"""
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

    `session_open` is the source's own reference for the daily-change column.
    Massive supplies the real session open (`day.o`); the simulator supplies the
    first price it minted this process. `None` means "no opinion" — the cache then
    pins the session open to the first price it sees.
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

    def to_payload(self) -> dict:
        return {"ts": iso_z(self.ts), "price": round(self.price, 4)}


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601, millisecond precision, 'Z' suffix — PLAN.md §7 conventions.

    One formatter, used by every timestamp the backend emits, so lexicographic order
    always equals chronological order and the frontend has exactly one format to parse.

    >>> iso_z(datetime(2026, 8, 26, 14, 3, 7, 412_000, tzinfo=timezone.utc))
    '2026-08-26T14:03:07.412Z'
    """
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def utcnow() -> datetime:
    """The module's only clock. Patch this in tests rather than datetime itself."""
    return datetime.now(timezone.utc)
```

**Why `utcnow()` exists.** Three files need "now" (`simulator.step`, `massive.grouped_daily`,
the SSE heartbeat). Routing them through one function means a test that needs a frozen clock
patches `app.market.types.utcnow` once, instead of monkey-patching `datetime` in three
modules — which is both fragile and, for a C-implemented type, awkward.

`iso_z` is the **only** place a timestamp becomes a string. `PLAN.md` §7 makes the format
load-bearing: `ORDER BY recorded_at` on the snapshots table is only correct because every
writer emits this exact shape.

---

## 5. `seeds.py` — starting prices and deterministic synthesis

`PLAN.md` §6 requires two behaviours that pull in opposite directions: recognisable prices
for well-known tickers, and *never rejecting* a ticker the LLM invents. `seeds.py` resolves
that with a lookup table plus a SHA-256 fallback.

```python
# backend/app/market/seeds.py
"""Starting prices, drift, volatility and sector for the simulator.

Levels approximate reality as of mid-2026 — close enough to be plausible, and nobody
trades against them. Exactness is not the goal; recognisability is. A student who sees
AAPL near $190 and NVDA near $125 believes the terminal; the third decimal place buys
nothing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class Sector(str, Enum):
    """Drives the sector factor in the correlation model (MARKET_SIMULATOR.md §3)."""

    TECH = "tech"
    CONSUMER = "consumer"
    FINANCE = "finance"
    HEALTH = "health"
    ENERGY = "energy"
    INDUSTRIAL = "industrial"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TickerSpec:
    price: float          # seed and initial anchor, USD
    drift: float          # mu, annualised
    volatility: float     # sigma, annualised (before SIM_VOL_SCALE)
    sector: Sector
    beta: float = 1.0     # loading on the market factor


T, S = TickerSpec, Sector

SEED_PRICES: dict[str, TickerSpec] = {
    # ---- the ten defaults (PLAN.md §7) ----------------------------------
    "AAPL":  T(190.00, 0.09, 0.26, S.TECH,       1.05),
    "GOOGL": T(175.00, 0.10, 0.29, S.TECH,       1.10),
    "MSFT":  T(420.00, 0.11, 0.25, S.TECH,       0.95),
    "AMZN":  T(185.00, 0.10, 0.33, S.CONSUMER,   1.20),
    "TSLA":  T(245.00, 0.05, 0.62, S.CONSUMER,   1.60),
    "NVDA":  T(125.00, 0.18, 0.55, S.TECH,       1.55),
    "META":  T(510.00, 0.12, 0.36, S.TECH,       1.25),
    "JPM":   T(215.00, 0.06, 0.24, S.FINANCE,    0.90),
    "V":     T(280.00, 0.07, 0.21, S.FINANCE,    0.85),
    "NFLX":  T(680.00, 0.10, 0.38, S.CONSUMER,   1.20),
    # ---- names PLAN.md §6 calls out as likely additions ------------------
    "PYPL":  T( 68.00, 0.04, 0.40, S.FINANCE,    1.15),
    "AMD":   T(160.00, 0.13, 0.50, S.TECH,       1.50),
    "INTC":  T( 32.00, 0.01, 0.42, S.TECH,       1.10),
    "DIS":   T( 95.00, 0.05, 0.30, S.CONSUMER,   1.05),
    "BA":    T(180.00, 0.03, 0.42, S.INDUSTRIAL, 1.30),
    "WMT":   T( 72.00, 0.08, 0.19, S.CONSUMER,   0.60),
    "KO":    T( 62.00, 0.05, 0.16, S.CONSUMER,   0.55),
    # ---- mega-cap tech ---------------------------------------------------
    "AVGO":  T(178.00, 0.14, 0.44, S.TECH,       1.35),
    "ORCL":  T(215.00, 0.11, 0.32, S.TECH,       1.05),
    "CRM":   T(265.00, 0.08, 0.35, S.TECH,       1.20),
    "ADBE":  T(390.00, 0.07, 0.34, S.TECH,       1.15),
    "QCOM":  T(165.00, 0.09, 0.38, S.TECH,       1.25),
    "MU":    T(105.00, 0.12, 0.52, S.TECH,       1.45),
    "PLTR":  T( 48.00, 0.20, 0.70, S.TECH,       1.60),
    "QQQ":   T(570.00, 0.11, 0.22, S.TECH,       1.15),
    # ---- consumer & retail ----------------------------------------------
    "COST":  T(880.00, 0.09, 0.20, S.CONSUMER,   0.75),
    "HD":    T(360.00, 0.06, 0.23, S.CONSUMER,   0.95),
    "MCD":   T(295.00, 0.05, 0.18, S.CONSUMER,   0.65),
    "NKE":   T( 78.00, 0.03, 0.31, S.CONSUMER,   1.00),
    "SBUX":  T( 92.00, 0.04, 0.29, S.CONSUMER,   0.95),
    "PG":    T(168.00, 0.05, 0.17, S.CONSUMER,   0.55),
    "F":     T( 11.50, 0.02, 0.38, S.CONSUMER,   1.20),
    "GM":    T( 48.00, 0.04, 0.36, S.CONSUMER,   1.25),
    "UBER":  T( 74.00, 0.12, 0.41, S.CONSUMER,   1.30),
    "ABNB":  T(135.00, 0.08, 0.42, S.CONSUMER,   1.25),
    "SHOP":  T( 88.00, 0.13, 0.55, S.CONSUMER,   1.50),
    "RIVN":  T( 14.00, 0.02, 0.75, S.CONSUMER,   1.60),
    "LCID":  T(  3.20, -0.05, 0.75, S.CONSUMER,  1.60),
    # ---- finance ---------------------------------------------------------
    "BRKB":  T(465.00, 0.08, 0.18, S.FINANCE,    0.80),
    "MA":    T(475.00, 0.08, 0.22, S.FINANCE,    0.90),
    "GS":    T(510.00, 0.07, 0.28, S.FINANCE,    1.15),
    "BAC":   T( 42.00, 0.05, 0.29, S.FINANCE,    1.10),
    "WFC":   T( 62.00, 0.05, 0.30, S.FINANCE,    1.10),
    "SQ":    T( 68.00, 0.09, 0.58, S.FINANCE,    1.55),
    "SOFI":  T( 10.50, 0.10, 0.66, S.FINANCE,    1.60),
    "COIN":  T(215.00, 0.15, 0.75, S.FINANCE,    1.60),
    # ---- health ----------------------------------------------------------
    "LLY":   T(820.00, 0.15, 0.30, S.HEALTH,     0.75),
    "UNH":   T(520.00, 0.07, 0.27, S.HEALTH,     0.70),
    "JNJ":   T(158.00, 0.04, 0.16, S.HEALTH,     0.60),
    "PFE":   T( 28.00, 0.02, 0.25, S.HEALTH,     0.65),
    "MRK":   T(112.00, 0.06, 0.22, S.HEALTH,     0.65),
    "ABBV":  T(190.00, 0.07, 0.23, S.HEALTH,     0.65),
    # ---- energy ----------------------------------------------------------
    "XOM":   T(118.00, 0.05, 0.28, S.ENERGY,     0.85),
    "CVX":   T(155.00, 0.04, 0.27, S.ENERGY,     0.85),
    # ---- industrial / telecom -------------------------------------------
    "T":     T( 22.00, 0.03, 0.22, S.INDUSTRIAL, 0.70),
    "VZ":    T( 41.00, 0.03, 0.21, S.INDUSTRIAL, 0.70),
    # ---- broad market ----------------------------------------------------
    "SPY":   T(640.00, 0.09, 0.16, S.OTHER,      1.00),
}

del T, S   # the aliases exist only to keep the table readable

_MIN_PRICE, _MAX_PRICE = 20.0, 500.0


def spec_for(ticker: str) -> TickerSpec:
    """Return a spec for ANY validated symbol. Never raises, never rejects.

    PLAN.md §6: the LLM manages the watchlist proactively, so rejecting an unknown
    ticker mid-conversation is a worse experience than a plausible synthetic price.
    Format validation (^[A-Z]{1,5}$) already happened at the API boundary; by the
    time we get here the symbol is acceptable by definition.

    The table always wins over the hash. AMD is in the table at $160; `_synthesise`
    would have said $104.85, and that number must never surface.
    """
    known = SEED_PRICES.get(ticker)
    return known if known is not None else _synthesise(ticker)


def _synthesise(ticker: str) -> TickerSpec:
    """Deterministic pseudo-random spec derived from the symbol itself.

    SHA-256, not the builtin hash(): Python's hash() is salted per process by
    PYTHONHASHSEED, so it would give a different price on every restart and break
    E2E reproducibility outright. This is the single most important line in the file.
    """
    h = hashlib.sha256(ticker.encode()).digest()
    price_n = int.from_bytes(h[0:8], "big")
    vol_n = int.from_bytes(h[8:12], "big")
    drift_n = int.from_bytes(h[12:16], "big")
    beta_n = int.from_bytes(h[16:20], "big")

    span = int((_MAX_PRICE - _MIN_PRICE) * 100) + 1   # cents in [20.00, 500.00]
    # Every field is rounded. Without it the top of each range lands one ULP over
    # the bound -- 0.20 + 4000/10000 == 0.6000000000000001 -- and a range assertion
    # written the obvious way fails on exactly one symbol in 4,001.
    return TickerSpec(
        price=round(_MIN_PRICE + (price_n % span) / 100.0, 2),
        drift=round(-0.05 + (drift_n % 2501) / 10_000.0, 4),    # -5% ... +20%
        volatility=round(0.20 + (vol_n % 4001) / 10_000.0, 4),  # 20% ... 60%
        sector=Sector.OTHER,
        beta=round(0.70 + (beta_n % 901) / 1_000.0, 3),         # 0.70 ... 1.60
    )
```

### Verified properties of `_synthesise`

Computed over all 17,576 three-letter symbols: **min $20.00, max $499.95, mean $259.93** —
uniform across the range, as intended. The `$20–$500` bound is a property of
**`_synthesise`**, not of `spec_for`: a range sweep must call the former, or it will trip
over `LLY` ($820), `UNH` ($520), `SPY` ($640) and `QQQ` ($570) in the table. Reference
values for test literals:

| Symbol | In table? | `_synthesise` price | `spec_for` price |
|---|---|---|---|
| `AAPL` | yes | — | **190.00** (table) |
| `PYPL` | yes | 178.51 | **68.00** (table) |
| `AMD` | yes | 104.85 | **160.00** (table) |
| `SNOW` | **no** | 348.97 | **348.97** |
| `COIN` | yes | 48.98 | **215.00** (table) |
| `ZZZZ` | **no** | 355.09 | **355.09** |

> ⚠ `MARKET_SIMULATOR.md` §10 proposes the assertion `spec_for("PYPL").price == 178.51`.
> That is **wrong** — PYPL is in `SEED_PRICES` at $68.00, so the table wins and the test
> would fail against correct code. Use a symbol that is genuinely absent from the table:
> `spec_for("SNOW").price == 348.97`. Recorded in §16.

Every entry in `SEED_PRICES` is a **known** symbol, so the `$20–$500` bound in `PLAN.md` §6
constrains only the synthetic path. Table entries deliberately fall outside it — NFLX at
$680, LLY at $820, F at $11.50 — because recognisability beats a range that exists purely
to stop the hash producing $0.03 or $40,000.

---

## 6. `source.py` — the unified interface

This is the seam. Both implementations satisfy it; the service knows nothing else.

```python
# backend/app/market/source.py
"""The abstract market data source.

Contract, in full:

  * `fetch()` performs ONE round of work and returns whatever it learned.
  * `fetch()` MAY raise. The caller (MarketDataService) owns retries, backoff and
    fallback. An implementation must not sleep, must not retry, must not swallow
    an error it cannot itself resolve.
  * `fetch()` may return fewer quotes than there are tracked tickers, or none at
    all. A missing ticker means "no news", never "the price is zero".
  * `set_tickers()` is synchronous, cheap and I/O-free. Work implied by a new
    ticker happens lazily inside the next `fetch()`.
  * All methods are called from a single asyncio task; no internal locking needed.
  * `aclose()` is idempotent and must not raise.
"""
from __future__ import annotations

import abc

from .types import Quote


class MarketDataSource(abc.ABC):
    #: stable identifier surfaced in /api/health and in logs: "simulator" | "massive"
    name: str

    #: seconds between fetch() calls. Re-read by the service AFTER every fetch, so an
    #: implementation may raise its own interval (e.g. MassiveSource after a 429).
    poll_interval: float

    #: Non-None when the source is working but cannot deliver moving prices (e.g. an
    #: end-of-day free tier). The stream is marked `degraded` even though nothing failed,
    #: and this string is what the frontend shows on hover. See PLAN.md §10.
    degraded_reason: str | None = None

    async def start(self) -> None:
        """Open connections, probe entitlement, resolve dates. May raise."""

    async def aclose(self) -> None:
        """Release resources. Idempotent; must not raise."""

    def prime(self, tickers: frozenset[str]) -> list[Quote]:
        """Optional: quotes a brand-new ticker can be given *without* a fetch.

        Default: nothing. A real-data source must never invent a price, so
        MassiveSource keeps this default and a newly added ticker legitimately
        has no price until the next poll (MARKET_INTERFACE.md §7).

        SimulatedSource overrides it to return the seed price immediately, which
        is not an invention — the seed price is the ticker's opening price by
        definition. See §7.4.
        """
        return []

    @abc.abstractmethod
    def set_tickers(self, tickers: frozenset[str]) -> None:
        """Replace the tracked set. Called on every watchlist/position change."""

    @abc.abstractmethod
    async def fetch(self) -> list[Quote]:
        """One poll round."""
```

### Why `fetch()` rather than an async generator

Restated from `MARKET_INTERFACE.md` §3 because it is the decision an implementer is most
likely to "improve":

- **Retry policy lives in one place.** `PLAN.md` §6 specifies exponential backoff capped at
  60 s and permanent fallback after three consecutive failures. As a `fetch()` caller that
  is ~20 lines in `service.py` covering every source ever written. As generators, each
  source reimplements it, and the simulator's copy is dead code nobody tests.
- **Tests need no clock.** `await source.fetch()` returns a list. Asserting on GBM output
  or snapshot parsing needs no event loop tricks, no `anext()`, no cancellation handling.
- **The fallback swap is one assignment between polls.** Interrupting a half-consumed
  generator is not.

### Why `prime()` is on the interface rather than an `isinstance` check in the service

`MarketDataService` must not branch on source type — the moment it does, the seam leaks.
`prime()` defaults to `[]`, so adding it costs `MassiveSource` nothing, and the service
calls it unconditionally after every `set_tracked()`. This is an addition to
`MARKET_INTERFACE.md`; §16 records why.

---

## 7. `simulator.py` — the default source

### 7.1 The model

Three layers on the **log price**, per `MARKET_SIMULATOR.md` §2:

```
log S_i(t+dt) - log S_i(t)  =  k_i*(log A_i - log S_i(t))*dt   <- 1. mean reversion (OU)
                             + (mu_i - sigma_i^2/2)*dt          <- 2. drift
                             + sigma_i*sqrt(dt)*Z_i             <- 3. diffusion
                             + J_i(t)                           <- 4. jump (rare)
```

Layers 2+3 are textbook GBM — exactly what `PLAN.md` §6 asks for. Layer 1 keeps a
multi-hour demo from wandering to $47 or $900. Layer 4 is the drama.

### 7.2 Calibration — the numbers, verified

`dt = 0.5 / (252 * 6.5 * 3600) = 8.479e-8` years per tick. Per-tick standard deviation is
`sigma * sqrt(dt)`:

| sigma (annual) | `SIM_VOL_SCALE` | per tick | over 5 min | over 1 h | over 6.5 h |
|---|---|---|---|---|---|
| 0.35 | 1.0 (honest) | 0.0102% (~$0.02 on $190) | 0.25% | 0.86% | 2.20% |
| **0.35** | **4.0 (default)** | **0.0408% (~$0.08 on $190)** | **1.00%** | **3.46%** | **8.82%** |
| 0.60 | 4.0 | 0.0699% (~$0.13 on $190) | 1.71% | 5.93% | 15.12% |

At scale 1.0 the daily-change column sits at ±0.2% forever and the flash animation fires on
sub-penny wiggles — the terminal reads as *stuck*. At scale 4.0 a ticker moves a visible ~1%
over five minutes. **This is a deliberate, documented lie in service of the demo, not a
modelling error.** `SIM_VOL_SCALE=1.0` restores honesty.

Mean reversion, `kappa = ln(2) / (half_life_hours / (6.5*252))`:

| Half-life | kappa (annual) | Stationary spread at sigma_eff = 1.4 |
|---|---|---|
| 2 h | 568 | ±4.15% |
| **4 h (default)** | **284** | **±5.88%** |
| 8 h | 142 | ±8.31% |
| 0 (disabled) | 0 | unbounded — pure GBM, the mode the analytic test uses |

### 7.2.1 Jumps dominate the variance budget — and every statistical test must know it

This is not obvious from the parameters and it invalidates the statistical tests as
`MARKET_SIMULATOR.md` §10 writes them, so it is called out here rather than discovered
during implementation.

A jump fires with probability `p = 0.0005` per ticker per tick and is `±U(0.02, 0.05)` in
log space, so it contributes `p·E[J²] = 0.0005 × 1.300e-3 = 6.50e-7` of variance per tick.
The diffusion term contributes `(sigma·scale)²·dt`. Measured:

| Ticker | sigma_eff | diffusion var/tick | **jump share of total variance** |
|---|---|---|---|
| AAPL @ scale 1.0 | 0.26 | 5.73e-9 | **99.1%** |
| AAPL @ scale 4.0 | 1.04 | 9.17e-8 | **87.6%** |
| KO @ scale 4.0 | 0.64 | 3.47e-8 | **94.9%** |
| NVDA @ scale 4.0 | 2.20 | 4.10e-7 | **61.3%** |
| TSLA @ scale 4.0 | 2.48 | 5.22e-7 | **55.5%** |

Three consequences, all load-bearing:

1. **The analytic GBM test must run with `event_prob=0.0`.** Otherwise the sample variance
   comes out ~130× the closed form at scale 1.0 and the test fails against *correct* code.
2. **The correlation test must too.** Jumps are drawn independently per ticker, so they
   dilute measured correlation by exactly the diffusion's variance share: with events on,
   AAPL/MSFT measures **0.004**, not 0.40. Verified.
3. **The on-screen effect `PLAN.md` §6 asks for is unaffected.** 99.95% of ticks contain no
   jump at all, so a *typical* tick is pure correlated diffusion and the watchlist still
   flashes red together. The variance decomposition is dominated by rare large moves; the
   tick-to-tick visual is dominated by the common factors. Both statements are true at once.

That the engine takes `event_prob` as a constructor argument (§7.4) exists precisely so
these tests can isolate the diffusion. Do not delete it as unused.

### 7.3 Correlation

One market factor, one factor per sector, plus idiosyncratic noise:

```
             sqrt(w_m)*bh_i*Z_market + sqrt(w_s)*Z_sector(i) + sqrt(w_idio)*Z_idio_i
    Z_i  =  ------------------------------------------------------------------------
                          n_i  =  sqrt(w_m*bh_i^2 + w_s + w_idio)

    bh_i  =  beta_i / MEAN_BETA          w_m = 0.25, w_s = 0.15, w_idio = 0.60
```

Two normalisations, doing two different jobs:

**`bh_i` = beta divided by `MEAN_BETA`, a module CONSTANT** (the mean beta over
`SEED_PRICES`, ≈ 1.08) — *not* the mean over the current watchlist. Using the watchlist mean
would make AAPL's volatility and its correlations change the moment someone adds TSLA,
which is indefensible: measured, NVDA's realised sigma moves **18.8%** between a high-beta
watchlist and a mixed one under the per-watchlist mean, versus **0.53%** under the constant.

**`n_i` divides the whole factor blend so `Var(Z_i) = 1` exactly, for every ticker.** The
weights summing to 1 is *not* on its own enough — `Var = w_m·bh_i² + w_s + w_idio` equals 1
only when `bh_i = 1`. Without the `n_i` divisor a beta-1.6 name runs ~18% hotter than the
`volatility` its table row claims. With it, measured realised sd over theoretical is
**1.002 / 1.003 / 0.999** for AAPL / NVDA / KO — the table means exactly what it says.

The closed form for a pair, which the test asserts against:

```
rho_ij = (w_m*bh_i*bh_j + (w_s if same sector else 0)) / (n_i * n_j)
```

Measured over 80,000 ticks with `event_prob=0.0` and `half_life_hours=0`:

| Pair | Sectors | Measured | Closed form |
|---|---|---|---|
| AAPL / MSFT | tech / tech | 0.379 | 0.377 |
| NVDA / MSFT | tech / tech | 0.424 | 0.426 |
| AAPL / NVDA | tech / tech | 0.452 | 0.447 |
| AAPL / JPM | tech / finance | 0.211 | 0.212 |
| AAPL / KO | tech / consumer | 0.138 | 0.138 |

For a pair at the universe mean beta these collapse to exactly the **0.40** same-sector and
**0.25** cross-sector headline of `MARKET_SIMULATOR.md` §3; the spread around them is
beta-driven, and high-beta pairs being *more* correlated than low-beta ones is realistic
rather than a defect. These are close to real S&P pairwise numbers and, far more
importantly, they are visibly correlated on screen — during a market-factor down-tick most
of the watchlist flashes red at once, which is the effect `PLAN.md` §6 asks for.

### 7.4 The code

```python
# backend/app/market/simulator.py
"""Correlated GBM with OU mean reversion and Poisson jumps.

FinAlly's DEFAULT price source, not a fallback (PLAN.md §6): free real data is
end-of-day and does not move, and outside US market hours even paid data is static.
The simulator is what makes the terminal worth watching.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from .seeds import Sector, TickerSpec, spec_for
from .source import MarketDataSource
from .types import Quote, utcnow

log = logging.getLogger(__name__)

TRADING_YEAR_SECONDS = 252 * 6.5 * 3600     # 5,896,800
MARKET_HOURS_PER_YEAR = 6.5 * 252           # 1,638

MARKET_WEIGHT = 0.25
SECTOR_WEIGHT = 0.15
IDIO_WEIGHT = 1.0 - MARKET_WEIGHT - SECTOR_WEIGHT   # 0.60

EVENT_PROB_PER_TICK = 0.0005                # ~one event per ticker per 1,000 s
EVENT_MIN_PCT, EVENT_MAX_PCT = 0.02, 0.05   # PLAN.md §6: "sudden 2-5% moves"
EVENT_ANCHOR_SHARE = 0.5                    # half the jump persists

#: Mean beta over SEED_PRICES. A CONSTANT, deliberately not the mean over the current
#: watchlist: a ticker's volatility and correlations must not change because someone
#: else added TSLA. See §7.3.
MEAN_BETA = 1.08

MIN_PRICE = 0.01


@dataclass(slots=True)
class _TickerState:
    spec: TickerSpec
    log_price: float
    log_anchor: float
    session_open: float


class GbmEngine:
    """Pure, synchronous, single-threaded. No I/O, no global RNG, no environment."""

    def __init__(
        self,
        *,
        seed: int | None = None,
        dt_seconds: float = 0.5,
        vol_scale: float = 4.0,
        half_life_hours: float = 4.0,
        event_prob: float = EVENT_PROB_PER_TICK,
    ) -> None:
        self._rng = random.Random(seed)
        self._event_prob = event_prob          # 0.0 isolates the diffusion; see §7.2.1
        self._dt = dt_seconds / TRADING_YEAR_SECONDS
        self._sqrt_dt = math.sqrt(self._dt)
        self._vol_scale = vol_scale
        self._kappa = (
            math.log(2) / (half_life_hours / MARKET_HOURS_PER_YEAR)
            if half_life_hours > 0
            else 0.0
        )
        self._states: dict[str, _TickerState] = {}

    # ---- lifecycle ---------------------------------------------------

    def ensure(self, tickers: frozenset[str]) -> None:
        """Mint state for symbols we have not seen. Idempotent, O(new), no RNG."""
        for t in sorted(tickers - self._states.keys()):
            spec = spec_for(t)
            lp = math.log(spec.price)
            self._states[t] = _TickerState(
                spec=spec, log_price=lp, log_anchor=lp, session_open=spec.price
            )

    def forget(self, keep: frozenset[str]) -> None:
        """Drop state for tickers that left the tracked set."""
        for t in list(self._states):
            if t not in keep:
                del self._states[t]

    def seed_quotes(self, tickers: frozenset[str]) -> list[Quote]:
        """Current state as quotes, consuming NO randomness. Backs `prime()`."""
        self.ensure(tickers)
        now = utcnow()
        return [
            Quote(
                ticker=t,
                price=max(MIN_PRICE, round(math.exp(self._states[t].log_price), 2)),
                ts=now,
                session_open=self._states[t].session_open,
            )
            for t in sorted(tickers)
        ]

    # ---- one tick -----------------------------------------------------

    def step(self, tickers: frozenset[str]) -> list[Quote]:
        self.ensure(tickers)
        if not tickers:
            return []                      # draw no randomness at all

        ordered = sorted(tickers)          # determinism: fixed RNG consumption order
        z_market = self._rng.gauss(0.0, 1.0)
        sectors = sorted({self._states[t].spec.sector for t in ordered})
        z_sector = {s: self._rng.gauss(0.0, 1.0) for s in sectors}

        now = utcnow()

        quotes: list[Quote] = []
        for t in ordered:
            st = self._states[t]
            z_idio = self._rng.gauss(0.0, 1.0)
            # bh scales the market loading; norm restores Var(z) == 1 exactly, so
            # `sigma` below means precisely what the seed table says. See §7.3.
            bh = st.spec.beta / MEAN_BETA
            norm = math.sqrt(MARKET_WEIGHT * bh * bh + SECTOR_WEIGHT + IDIO_WEIGHT)
            z = (
                math.sqrt(MARKET_WEIGHT) * bh * z_market
                + math.sqrt(SECTOR_WEIGHT) * z_sector[st.spec.sector]
                + math.sqrt(IDIO_WEIGHT) * z_idio
            ) / norm

            sigma = st.spec.volatility * self._vol_scale
            mu = st.spec.drift

            # the anchor drifts with mu, so a trending ticker is not yanked back
            st.log_anchor += mu * self._dt

            st.log_price += (
                self._kappa * (st.log_anchor - st.log_price) * self._dt   # 1. reversion
                + (mu - 0.5 * sigma * sigma) * self._dt                   # 2. drift
                + sigma * self._sqrt_dt * z                               # 3. diffusion
            )

            # 4. jump — Poisson arrival, checked per ticker per tick
            if self._rng.random() < self._event_prob:
                jump = self._rng.choice((1.0, -1.0)) * self._rng.uniform(
                    EVENT_MIN_PCT, EVENT_MAX_PCT
                )
                st.log_price += jump
                st.log_anchor += jump * EVENT_ANCHOR_SHARE
                log.info("simulated event: %s %+.2f%%", t, jump * 100)

            price = max(MIN_PRICE, round(math.exp(st.log_price), 2))
            quotes.append(
                Quote(ticker=t, price=price, ts=now, session_open=st.session_open)
            )
        return quotes


class SimulatedSource(MarketDataSource):
    """The MarketDataSource wrapper. Thin on purpose — all the model is in GbmEngine."""

    name = "simulator"

    def __init__(
        self,
        *,
        seed: int | None = None,
        interval: float = 0.5,
        vol_scale: float = 4.0,
        half_life_hours: float = 4.0,
        event_prob: float = EVENT_PROB_PER_TICK,
    ) -> None:
        self.poll_interval = interval
        self.degraded_reason = None        # the simulator is never degraded
        self._engine = GbmEngine(
            seed=seed,
            dt_seconds=interval,
            vol_scale=vol_scale,
            half_life_hours=half_life_hours,
            event_prob=event_prob,
        )
        self._tickers: frozenset[str] = frozenset()

    def set_tickers(self, tickers: frozenset[str]) -> None:
        self._engine.ensure(tickers)       # mint seed prices for new symbols
        self._engine.forget(tickers)       # release state for departed ones
        self._tickers = tickers

    def prime(self, tickers: frozenset[str]) -> list[Quote]:
        return self._engine.seed_quotes(tickers & self._tickers)

    async def fetch(self) -> list[Quote]:
        return self._engine.step(self._tickers)   # pure CPU, never raises
```

### 7.5 Implementation notes that are easy to get wrong

- **`fetch()` is synchronous work behind `async def`, and that is correct.** One GBM step
  over ≤30 tickers is microseconds. Do **not** offload it to a thread: the overhead exceeds
  the work, and determinism (§7.6) depends on single-threaded RNG ordering.
- **`round(..., 2)` is applied to the *emitted* price, never fed back into `log_price`.**
  Rounding the state injects a bias on every tick and, on a cheap stock like LCID at $3.20,
  quantises the walk into a visible staircase. The internal path stays continuous.
- **`session_open` is captured once**, when a ticker first appears, and never updated. It is
  the daily-change reference (`PLAN.md` §6). A ticker added mid-session therefore opens at
  exactly 0.00% change — honest for a simulated ticker that did not exist a second ago.
- **`MIN_PRICE` is a seatbelt, not a clamp on the log path.** Log-space GBM cannot reach
  zero; the floor only guards a pathological config.
- **`ensure()` and `forget()` are separate calls** so `set_tickers` can grow and shrink
  without the two operations racing over one iteration of the dict.
- **A removed-then-re-added ticker restarts at its seed price.** `forget()` drops engine
  state and `PriceCache.evict()` drops the ring buffer at the same moment, so the two stay
  consistent. Deliberate: the alternative is unbounded state for tickers nobody watches.
  Note that a *held* ticker is never in this position — the tracked set is
  `watchlist ∪ held`, so removing it from the watchlist does not evict it (`PLAN.md` §6).

### 7.6 Determinism — three separate guarantees

`MARKET_SIMULATOR.md` §6, restated because these are routinely conflated:

1. **Seed prices are always deterministic**, in every mode, from the symbol alone.
   `SEED_PRICES["AAPL"] = 190.00` and `_synthesise("SNOW") → 348.97` never change between
   runs, processes, or `PYTHONHASHSEED` values.
2. **The random path is deterministic when `SIM_SEED` is set.** Identical ticker sets in
   identical order produce byte-identical sequences. E2E runs set `SIM_SEED=42`.
3. **Production leaves `SIM_SEED` unset** and seeds from OS entropy, so two people watching
   the same demo do not see the same chart.

Rules that make guarantee 2 hold — all four are load-bearing:

- **Never call module-level `random.*`.** One `random.Random` instance owned by the engine.
  The module functions share global state with everything else in the process (pytest
  plugins included) and destroy reproducibility in ways that are miserable to debug.
- **Iterate tickers in sorted order everywhere RNG is consumed.** `set` iteration order is
  not stable across processes.
- **Draw a fixed number of normals per tick, in a fixed order** — market, then sectors
  (sorted), then idiosyncratic (sorted tickers). Never skip a draw.
- **The engine is single-threaded.** §7.5 already forbids threading `step()`; this is why.

`prime()` / `seed_quotes()` consume **no** randomness, which is the only reason adding them
does not break guarantee 2. Any future helper must hold to the same rule.

### 7.7 What the simulator deliberately does not model

So nobody files these as bugs, and so a student asking "is this real?" gets a straight
answer: no volume, bid/ask or order book (`PLAN.md` is market-orders-only for exactly this
reason); no market hours, holidays, gaps or opening auctions — it trades continuously, and a
real market's overnight gap is a large share of daily variance and is simply absent; no fat
tails or volatility clustering (real returns are leptokurtic and heteroskedastic, GBM is
neither — the jump process is a crude stand-in, crude on purpose); no corporate actions,
earnings, splits or dividends; no macro structure beyond the single market factor. And the
default `SIM_VOL_SCALE=4.0` overstates volatility fourfold. Everything computed on top —
P&L, the heatmap, the LLM's analysis — is arithmetically correct on prices that move four
times faster than a real market's.

---

## 8. `massive.py` — the optional real-data source

### 8.1 The one fact that shapes this file

**The free tier cannot call the snapshot endpoints at all** (`MASSIVE_API.md` §3). It gets a
`403`, not a `401`, and it is capped at 5 requests/minute. So the implementation cannot be
"call the snapshot endpoint"; it must probe entitlement once and run one of two modes.

```
                       +- MASSIVE_API_KEY unset/empty/placeholder -> Simulator (default)
  startup -------------+
                       +- key set -> probe: GET /v2/snapshot/...?tickers=AAPL
                                       |
                           200 --------+--> SNAPSHOT mode   (Starter+, 15 s poll)
                           403 --------+--> GROUPED  mode   (free tier, 60 s poll, EOD)
                           401 --------+--> log "bad key" -> Simulator, degraded
                           5xx/timeout +--> propagate -> service falls back, degraded
```

| | SNAPSHOT mode | GROUPED mode |
|---|---|---|
| Endpoint | `/v2/snapshot/locale/us/markets/stocks/tickers?tickers=…` | `/v2/aggs/grouped/locale/us/market/stocks/{date}` |
| Plan | Starter and above | Basic (free) and above |
| Poll interval | 15 s (4 calls/min) | 60 s (1 of the 5 allowed calls/min) |
| Covers | exactly the tracked set | every US ticker — new watchlist entries cost no extra call |
| Recency | 15-min delayed, or real-time on Advanced | end-of-day: **prices do not change** |
| Response size | a few KB | 1–3 MB — parse, keep the tracked set, discard the rest |
| Stream status | `connected` | **`degraded`** — the dot tells the truth (`PLAN.md` §10) |

**GROUPED mode is honest but static.** Every quote after the first is identical, so
`PriceCache.apply` returns `None`, `direction` never leaves `flat`, and no flash animation
fires. That is correct. Do not fake movement.

### 8.2 Timestamp units are not uniform

The single most common way to get a date in the year 52,000:

| Field | Unit |
|---|---|
| `updated`, `lastTrade.t`, `lastQuote.t` | **nanoseconds** |
| `day.t`, `min.t`, grouped `t` | **milliseconds** |

Normalise on ingest, in one helper each.

### 8.3 The client

```python
# backend/app/market/massive.py
"""Massive (formerly Polygon.io) REST market data client and source.

We use httpx directly rather than the `massive` SDK: the SDK's RESTClient is
synchronous and paginates with blocking generators, so calling it from the event
loop would stall the SSE stream, and wrapping it in asyncio.to_thread buys nothing
over just issuing the request. We need exactly two endpoints.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .source import MarketDataSource
from .types import Quote, utcnow

log = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.massive.com"
EASTERN = ZoneInfo("America/New_York")     # stdlib; correct across DST

SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"
GROUPED_PATH = "/v2/aggs/grouped/locale/us/market/stocks/{date}"
PREV_PATH = "/v2/aggs/ticker/{ticker}/prev"
AGGS_PATH = "/v2/aggs/ticker/{ticker}/range/1/minute/{start}/{end}"
MARKET_STATUS_PATH = "/v1/marketstatus/now"


class MassiveError(RuntimeError):
    """Base for every error this client raises on purpose."""


class MassiveAuthError(MassiveError):
    """401 — key missing or wrong. Terminal; retrying will never help."""


class MassiveEntitlementError(MassiveError):
    """403 — the plan does not cover this endpoint. Terminal for THIS endpoint."""


class MassiveRateLimitError(MassiveError):
    """429 — the poll interval is wrong, not just this one request unlucky."""


class MassiveClient:
    def __init__(self, api_key: str, *, base_url: str = MASSIVE_BASE_URL) -> None:
        # Bearer header, not ?apiKey= : we log request paths on failure, and a key in
        # the query string ends up in container logs and in exception text.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> dict:
        resp = await self._client.get(path, params=params or None)
        rid = resp.headers.get("x-request-id", "?")
        # Branch on the STATUS CODE, never on body text: the 401 bodies are verified,
        # the 403 body shape is reported but unverified (MASSIVE_API.md §6).
        if resp.status_code == 401:
            raise MassiveAuthError(f"{path}: bad or missing API key (request_id={rid})")
        if resp.status_code == 403:
            raise MassiveEntitlementError(
                f"{path}: not entitled on this plan (request_id={rid})"
            )
        if resp.status_code == 429:
            raise MassiveRateLimitError(f"{path}: rate limited (request_id={rid})")
        resp.raise_for_status()            # 5xx -> HTTPStatusError -> service backoff
        body = resp.json()
        if body.get("status") not in (None, "OK", "DELAYED"):
            log.warning(
                "massive: %s returned status=%s request_id=%s", path, body.get("status"), rid
            )
        return body

    # ---- SNAPSHOT mode (Starter and above) ------------------------------

    async def snapshot(self, tickers: set[str]) -> list[Quote]:
        """One call for the whole tracked set. `tickers` is case-sensitive upstream."""
        if not tickers:
            return []
        body = await self._get(SNAPSHOT_PATH, tickers=",".join(sorted(tickers)))
        quotes = [q for row in body.get("tickers", ()) if (q := _parse_snapshot_row(row))]
        # Unknown symbols are simply ABSENT from the response — there is no per-ticker
        # error object on v2. Diff and log; leave them on their cached value.
        missing = tickers - {q.ticker for q in quotes}
        if missing:
            log.debug("massive: no snapshot rows for %s", sorted(missing))
        return quotes

    # ---- GROUPED mode (free tier) ---------------------------------------

    async def grouped_daily(self, day: str, tickers: set[str]) -> list[Quote]:
        """One call returns every US ticker (~10k rows, 1-3 MB); keep what we track."""
        if not tickers:
            return []
        body = await self._get(GROUPED_PATH.format(date=day))
        # Stamp each quote with the BAR's timestamp (`t`, milliseconds), not utcnow().
        # Re-polling the same EOD bar must produce a byte-identical Quote so that
        # PriceCache.apply() recognises it as a repeat and returns None. With a
        # wall-clock stamp every poll would look like news and spam the SSE stream
        # with `flat` ticks and the ring buffer with duplicate points.
        return [
            Quote(
                ticker=r["T"],
                price=float(r["c"]),
                ts=_ms_to_dt(r.get("t")),
                session_open=float(r.get("o") or r["c"]),
            )
            for r in body.get("results", ())
            if r.get("T") in tickers and r.get("c")
        ]

    async def latest_trading_date(self, *, max_lookback: int = 5) -> str:
        """Walk back from 'today in ET' to the most recent date with data.

        There is no "latest" alias. Call this ONCE per process (and once per date
        rollover) and cache it — on the free tier it costs up to 5 of only 5
        requests per minute.
        """
        day = datetime.now(EASTERN).date()
        for _ in range(max_lookback):
            body = await self._get(GROUPED_PATH.format(date=day.isoformat()))
            if body.get("resultsCount", 0) > 0:
                return day.isoformat()
            day -= timedelta(days=1)
        raise MassiveError(f"no trading data in the last {max_lookback} days")

    async def market_status(self) -> dict:
        """Free and real-time on every plan. Explains WHY prices are frozen."""
        return await self._get(MARKET_STATUS_PATH)

    async def previous_close(self, ticker: str) -> float | None:
        """One ticker per call — one-off backfill only, never in the poll loop."""
        body = await self._get(PREV_PATH.format(ticker=ticker), adjusted="true")
        results = body.get("results") or []
        return float(results[0]["c"]) if results else None

    async def minute_bars(self, ticker: str, day: str, limit: int = 600) -> list[Quote]:
        """Optional chart backfill (§8.6). A minute with no trades produces no bar,
        so the series has gaps — never assume evenly spaced points."""
        body = await self._get(
            AGGS_PATH.format(ticker=ticker, start=day, end=day),
            adjusted="true",
            sort="asc",
            limit=limit,
        )
        rows = body.get("results") or []
        open_ = float(rows[0]["o"]) if rows else None
        return [
            Quote(ticker=ticker, price=float(r["c"]), ts=_ms_to_dt(r["t"]), session_open=open_)
            for r in rows
        ]


def _ns_to_dt(value: int | None) -> datetime:
    """`updated` / `lastTrade.t` are NANOseconds."""
    if not value:
        return utcnow()
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _ms_to_dt(value: int | None) -> datetime:
    """`day.t` / `min.t` / grouped `t` are MILLIseconds."""
    if not value:
        return utcnow()
    return datetime.fromtimestamp(value / 1_000, tz=timezone.utc)


def _parse_snapshot_row(row: dict) -> Quote | None:
    """lastTrade -> minute bar -> day bar -> previous close.

    Snapshot data is cleared daily at 3:30 AM ET and repopulates from ~4:00 AM ET.
    In that window `day` is zero-filled and `todaysChangePerc` is meaningless, so
    every one of these fallbacks earns its place — they are what stops the UI
    showing $0.00 at 3:45 AM.

    Note we do NOT use `todaysChange`/`todaysChangePerc`: our change column is
    measured against `session_open` by the cache, in one place, for both sources.
    """
    day = row.get("day") or {}
    minute = row.get("min") or {}
    prev = row.get("prevDay") or {}
    price = (
        (row.get("lastTrade") or {}).get("p")
        or minute.get("c")
        or day.get("c")
        or prev.get("c")
    )
    if not price:
        return None
    return Quote(
        ticker=row["ticker"],
        price=float(price),
        ts=_ns_to_dt(row.get("updated")),
        session_open=float(day.get("o") or prev.get("c") or price),
    )
```

### 8.4 The source

```python
SNAPSHOT, GROUPED = "snapshot", "grouped"
MAX_POLL_INTERVAL = 300.0


class MassiveSource(MarketDataSource):
    name = "massive"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = MASSIVE_BASE_URL,
        snapshot_interval: float = 15.0,
        grouped_interval: float = 60.0,
    ) -> None:
        self._client = MassiveClient(api_key, base_url=base_url)
        self._snapshot_interval = snapshot_interval
        self._grouped_interval = grouped_interval
        self._mode: str | None = None
        self._date: str | None = None          # cached trading date, GROUPED mode
        self._date_resolved_on: date | None = None
        self._tickers: frozenset[str] = frozenset()
        self.poll_interval = snapshot_interval
        self.degraded_reason = None

    def set_tickers(self, tickers: frozenset[str]) -> None:
        self._tickers = tickers                # no I/O; the next fetch() uses it

    async def aclose(self) -> None:
        await self._client.aclose()

    async def start(self) -> None:
        """Probe entitlement exactly once. 403 => free tier => GROUPED mode.

        A 401 propagates: MarketDataService catches it, logs "bad MASSIVE_API_KEY"
        and builds a SimulatedSource instead. A typo'd key must never take the app down.
        """
        try:
            await self._client.snapshot({"AAPL"})
        except MassiveEntitlementError:
            await self._switch_to_grouped("free tier: snapshots not entitled")
            return
        self._mode = SNAPSHOT
        self.poll_interval = self._snapshot_interval
        log.info("massive: SNAPSHOT mode, polling every %.0fs", self.poll_interval)

    async def fetch(self) -> list[Quote]:
        if self._mode == SNAPSHOT:
            try:
                return await self._client.snapshot(set(self._tickers))
            except MassiveEntitlementError as exc:
                # Terminal for this endpoint, NOT a failure: switch, do not back off.
                await self._switch_to_grouped(str(exc))
                return []
            except MassiveRateLimitError:
                self._raise_interval()
                raise
        await self._refresh_date_if_stale()
        try:
            return await self._client.grouped_daily(self._date, set(self._tickers))
        except MassiveRateLimitError:
            self._raise_interval()
            raise

    # ---- internals -------------------------------------------------------

    async def _switch_to_grouped(self, reason: str) -> None:
        self._mode = GROUPED
        self.poll_interval = self._grouped_interval
        self._date = await self._client.latest_trading_date()
        self._date_resolved_on = datetime.now(EASTERN).date()
        self.degraded_reason = "end-of-day data (free Massive tier)"
        log.warning(
            "massive: GROUPED mode (%s) — end-of-day prices for %s, polling every %.0fs",
            reason, self._date, self.poll_interval,
        )

    async def _refresh_date_if_stale(self) -> None:
        """Re-resolve at most once per ET calendar day. Without this, a container
        left running overnight serves yesterday's close forever."""
        today = datetime.now(EASTERN).date()
        if self._date_resolved_on == today:
            return
        self._date = await self._client.latest_trading_date()
        self._date_resolved_on = today
        log.info("massive: trading date is now %s", self._date)

    def _raise_interval(self) -> None:
        """A 429 means the INTERVAL is wrong, not that this request was unlucky.

        Monotonic on purpose: lowering it again on the next success would oscillate
        straight back into a 429.
        """
        old = self.poll_interval
        self.poll_interval = min(MAX_POLL_INTERVAL, self.poll_interval * 1.5)
        log.warning("massive: 429 — poll interval %.0fs -> %.0fs", old, self.poll_interval)
```

### 8.5 Reading the failure ladder onto this code

| Status | Meaning | Handled where | Counts as a failure? |
|---|---|---|---|
| `200` | OK | `_get` also checks the body's `"status"` | no |
| `401` | bad/missing key | raised out of `start()` → service falls back permanently | n/a — terminal |
| `403` | plan excludes the endpoint | **caught inside `fetch()`** → switch SNAPSHOT→GROUPED | **no** |
| `429` | rate limited | `_raise_interval()` then re-raise | **yes** |
| `5xx` | upstream problem | `raise_for_status()` | yes |
| timeout / `ConnectError` | network | propagates from httpx | yes |

Three consecutive failures ⇒ the service swaps in `SimulatedSource` permanently and marks
the stream `degraded` (`PLAN.md` §6). **The SSE stream must never die because the upstream
did**; across every row above, the last cached prices are kept and keep being served.

### 8.6 Optional: chart backfill

On a Massive-backed run the ring buffer starts empty, so `/api/history/{ticker}` returns a
flat line until the buffer fills — 2.5 hours at a 15 s poll. One call per ticker of
`1/minute` bars for the current trading day gives a real intraday shape instead.

Gate it behind **"backfill on first sight of a ticker"**, so it costs one call per ticker per
*process*, not per poll, and leave it **off by default** (`MASSIVE_BACKFILL=false`) — on a
free key, backfilling ten default tickers is ten calls against a 5/min budget, which
guarantees a 429 storm on startup. It is a nice-to-have for paid keys only.

```python
# inside MassiveSource, when MASSIVE_BACKFILL is enabled and mode == SNAPSHOT
async def _backfill(self, tickers: frozenset[str]) -> list[Quote]:
    out: list[Quote] = []
    for t in sorted(tickers - self._backfilled):
        try:
            out.extend(await self._client.minute_bars(t, self._trading_day()))
        except MassiveError as exc:
            log.info("massive: backfill skipped for %s: %s", t, exc)
        finally:
            self._backfilled.add(t)        # never retry — one call per ticker per process
    return out
```

Backfilled quotes flow through `PriceCache.apply` like any other, so the ring buffer ends up
in chronological order provided they are applied before the first live poll.

### 8.7 `scripts/check_massive.py`

Ship this so a student with a key finds out in five seconds which tier they are on, rather
than reading a wall of 403s in the container log.

```python
#!/usr/bin/env python
"""Which Massive tier is this key on? Run: uv run python scripts/check_massive.py"""
import asyncio, os, sys

import httpx

BASE = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")


async def main() -> int:
    key = (os.getenv("MASSIVE_API_KEY") or "").strip()
    if not key:
        print("MASSIVE_API_KEY is not set — FinAlly will use the simulator. That is fine.")
        return 0
    async with httpx.AsyncClient(
        base_url=BASE, headers={"Authorization": f"Bearer {key}"}, timeout=10.0
    ) as c:
        prev = await c.get("/v2/aggs/ticker/AAPL/prev")
        if prev.status_code == 401:
            print("✗ 401 — the key is not valid. FinAlly will fall back to the simulator.")
            return 1
        print(f"✓ key accepted (AAPL prev close: {prev.json()['results'][0]['c']})")

        snap = await c.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers", params={"tickers": "AAPL,MSFT"}
        )
        if snap.status_code == 200:
            print("✓ SNAPSHOT mode — Starter or above, 15 s polling, live-ish prices")
        elif snap.status_code == 403:
            print("● GROUPED mode — free tier: END-OF-DAY prices, 60 s polling.")
            print("  The connection dot will be YELLOW and prices will not move.")
            print("  Unset MASSIVE_API_KEY to use the simulator instead — it moves.")
        else:
            print(f"? unexpected {snap.status_code}: {snap.text[:200]}")

        st = (await c.get("/v1/marketstatus/now")).json()
        print(f"  market is {st.get('market')} (server time {st.get('serverTime')})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

---

## 9. `cache.py` — state and tick derivation

The cache is the **only** place a `Tick` is created, and the only place that knows both a
ticker's previous price and its session open.

```python
# backend/app/market/cache.py
"""In-memory price state. Single-process, never persisted, rebuilt from scratch on
restart — which is why the P&L chart shows straight segments across a container
restart (PLAN.md §7) and why /api/history starts empty.
"""
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
    def __init__(self) -> None:
        self._states: dict[str, TickerState] = {}

    def apply(self, quote: Quote) -> Tick | None:
        """Fold one quote into state and return the Tick to broadcast.

        Returns None when the quote carries no new information — an identical price
        at or before the timestamp we already hold. Callers MUST handle None: on a
        free-tier Massive key every quote after the first is a repeat.
        """
        st = self._states.get(quote.ticker)
        if st is None:
            open_ = quote.session_open if quote.session_open is not None else quote.price
            st = TickerState(
                ticker=quote.ticker,
                price=quote.price,
                prev_price=quote.price,      # the first tick is flat, by definition
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
            # A source may learn the real session open late (a Massive backfill);
            # accept a real value, never let a None drift it back.
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

### Two references, on purpose

`PLAN.md` §6 is explicit and this is the single easiest thing to get wrong:

| Field | Compared against | Drives |
|---|---|---|
| `direction` | `prev_price` — the **previous tick** | the flash animation |
| `change`, `change_pct` | `session_open` — the **session open** | the daily-change column |

Conflating them pins the change column at ±0.05% forever and makes the UI look broken. A
test asserts both independently (§17).

### The ring buffer is fed by the producer, not the broadcaster

One entry per **actual price change**, not one per 500 ms SSE frame. So 600 points is
~5 minutes of simulator history and ~2.5 hours of Massive SNAPSHOT history at a 15 s poll —
both useful chart seeds, and neither is 600 copies of the same number. In GROUPED mode the
ring legitimately holds **one point per trading day**, because that is all the free tier
knows; the chart is a dot, which is honest.

### Eviction drops history

`evict()` deletes the ring along with the state, so a ticker removed from the tracked set
and re-added later starts from an empty chart. Deliberate — the alternative is unbounded
memory for tickers nobody watches. A **held** ticker is never evicted: the tracked set is
`watchlist ∪ {non-zero positions}` (§13.3), so removing it from the watchlist leaves it
tracked, priced and charted. That union is exactly what `PLAN.md` §6 warns about.

---

## 10. `service.py` — the public surface

The only object the rest of the backend touches.

```python
# backend/app/market/service.py
from __future__ import annotations

import asyncio
import logging
import random

from .cache import PriceCache
from .simulator import SimulatedSource
from .source import MarketDataSource
from .types import PricePoint, StreamStatus, Tick

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3      # PLAN.md §6
BACKOFF_CAP_SECONDS = 60.0        # PLAN.md §6
BROADCAST_INTERVAL = 0.5          # PLAN.md §6 — the SSE cadence
SUBSCRIBER_QUEUE_SIZE = 64


class MarketDataService:
    """Owns the producer task, the price cache, the SSE fan-out and the failure ladder."""

    def __init__(
        self,
        source: MarketDataSource,
        *,
        fallback_factory=SimulatedSource,
        broadcast_interval: float = BROADCAST_INTERVAL,
    ) -> None:
        self._source = source
        self._fallback_factory = fallback_factory   # injectable so tests can assert on it
        self._broadcast_interval = broadcast_interval
        self._cache = PriceCache()
        self._tracked: frozenset[str] = frozenset()
        self._subscribers: set[asyncio.Queue[list[Tick]]] = set()
        self._pending: dict[str, Tick] = {}         # coalesced since the last broadcast
        self._status = StreamStatus.DISCONNECTED
        self._degraded_reason: str | None = None
        self._tasks: list[asyncio.Task] = []

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the producer. A source that cannot start is replaced, not fatal.

        MARKET_INTERFACE.md §8 says a 401 out of MassiveSource.start() means "log
        bad key and build a SimulatedSource instead" but does not say who catches
        it. It is caught HERE — one place, covering every start-time failure
        (bad key, DNS, a corporate proxy eating the request), so a typo in .env
        can never take the app down.
        """
        try:
            await self._source.start()
        except Exception as exc:                    # noqa: BLE001
            log.error("market: %s failed to start (%s) — using the simulator",
                      self._source.name, exc)
            await self._fall_back(f"startup failed: {exc}")
        else:
            self._apply_source_health()
        self._tasks = [
            asyncio.create_task(self._produce(), name="market-producer"),
            asyncio.create_task(self._broadcast(), name="market-broadcast"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self._source.aclose()
        self._status = StreamStatus.DISCONNECTED

    # ---- reads ----------------------------------------------------------

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
        """Surfaced by GET /api/health and on the SSE `status` event."""
        return {
            "source": self._source.name,
            "status": self._status.value,
            "reason": self._degraded_reason,
            "tracked": sorted(self._tracked),
            "poll_interval": self._source.poll_interval,
            "subscribers": len(self._subscribers),
        }

    # ---- tracked set -----------------------------------------------------

    def set_tracked(self, tickers: frozenset[str]) -> None:
        """watchlist ∪ {tickers with a non-zero position} — PLAN.md §6.

        Idempotent and safe to call on every mutation. MUST be called after every
        watchlist add/remove AND after every trade, or a held-but-unwatched ticker
        freezes and the positions table silently goes stale.
        """
        if tickers == self._tracked:
            return
        added = tickers - self._tracked
        self._tracked = tickers
        self._source.set_tickers(tickers)
        self._cache.evict(tickers)
        # Give a brand-new ticker a price immediately where the source can honestly
        # supply one (the simulator's seed price). MassiveSource returns [] and the
        # ticker legitimately has no price until the next poll.
        for quote in self._source.prime(added):
            tick = self._cache.apply(quote)
            if tick is not None:
                self._pending[tick.ticker] = tick

    @property
    def tracked(self) -> frozenset[str]:
        return self._tracked

    # ---- SSE fan-out -----------------------------------------------------

    def subscribe(self) -> asyncio.Queue[list[Tick]]:
        q: asyncio.Queue[list[Tick]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        snap = list(self._cache.snapshot().values())
        if snap:
            q.put_nowait(snap)      # a new client renders populated immediately
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- the producer ----------------------------------------------------

    async def _produce(self) -> None:
        """PLAN.md §3, background task 1. Poll, fold into the cache, apply the ladder."""
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
            except Exception as exc:            # noqa: BLE001 — nothing may kill this loop
                failures += 1
                log.warning(
                    "market: %s fetch failed (%d/%d): %s",
                    self._source.name, failures, MAX_CONSECUTIVE_FAILURES, exc,
                )
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    await self._fall_back(str(exc))
                    failures = 0
                    delay = self._source.poll_interval
                else:
                    # exponential backoff with jitter, capped at 60 s
                    delay = min(BACKOFF_CAP_SECONDS, 2.0**failures) * (0.5 + random.random())
            await asyncio.sleep(delay)

    async def _broadcast(self) -> None:
        """Emit coalesced ticks to every subscriber at a fixed cadence."""
        while True:
            await asyncio.sleep(self._broadcast_interval)
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

    # ---- degradation -----------------------------------------------------

    def _apply_source_health(self) -> None:
        reason = self._source.degraded_reason
        self._degraded_reason = reason
        self._status = StreamStatus.DEGRADED if reason else StreamStatus.CONNECTED

    async def _fall_back(self, reason: str) -> None:
        """Terminal, once per process — PLAN.md §6."""
        if isinstance(self._source, SimulatedSource):
            log.error("market: the simulator itself failed: %s", reason)
            return
        log.error(
            "market: %s failed, falling back to the simulator for the rest of the "
            "process: %s", self._source.name, reason,
        )
        old, self._source = self._source, self._fallback_factory()
        try:
            await old.aclose()
        except Exception:                       # noqa: BLE001 — aclose must not matter
            log.debug("market: error closing the old source", exc_info=True)
        await self._source.start()
        self._source.set_tickers(self._tracked)
        self._status = StreamStatus.DEGRADED
        self._degraded_reason = f"upstream unavailable, using the simulator ({reason})"
```

### Why coalescing is a `dict` keyed by ticker

Between two broadcasts the simulator produces exactly one quote per ticker, but a slow
broadcast or a burst of backfill quotes can produce several. Keeping only the newest per
ticker means the SSE frame count is bounded by the tracked set (≤30 + held), never by the
producer's rate. `_pending` is read and reassigned in the same synchronous block with no
`await` between, so there is no race with the producer on a single event loop.

### Why a dropped subscriber is the right answer to `QueueFull`

64 batches is ~32 seconds of buffer at the 500 ms cadence. A client that far behind is not
going to catch up; holding the batch would apply backpressure to the producer and stall the
stream for *everyone*. `EventSource` reconnects on its own, so the user sees a brief yellow
dot and recovers.

---

## 11. The failure ladder in one place

| Situation | Reaction | Resulting status |
|---|---|---|
| `fetch()` raises, attempt 1–2 | backoff `2^n × jitter`, cap 60 s; keep cached prices | `connected` |
| `fetch()` raises, attempt 3 | **swap in `SimulatedSource`, permanently** | `degraded` |
| `MassiveSource.start()` raises (401, DNS, proxy) | caught in `MarketDataService.start()` → fall back | `degraded` |
| Massive `403` on a poll | handled **inside** `MassiveSource`: SNAPSHOT → GROUPED, **not** a failure | `degraded` (EOD) |
| Massive `429` | source raises its own `poll_interval` **and** re-raises → counts as a failure | `connected` until 3 strikes |
| Source healthy but end-of-day | `degraded_reason` set, no failure counted | `degraded` |
| Service not started / stopped | — | `disconnected` |

Yellow means **"reconnecting *or* degraded"** per `PLAN.md` §10. A free-tier Massive key
that is working perfectly still shows yellow, because the prices genuinely are not live.
`health.reason` is the string the frontend shows on hover.

The fallback is **terminal for the process lifetime** — no "try Massive again in five
minutes". A demo that flips between live and simulated prices mid-lecture, silently
changing every number on screen, is worse than one that commits to the simulator and says
so in the dot.

---

## 12. `__init__.py` — configuration and source selection

The single place the environment is read.

```python
# backend/app/market/__init__.py
"""Public surface of the market module.

Everything outside this package imports from `app.market` (this file),
`app.market.service` or `app.market.types` — and nothing else.
"""
from __future__ import annotations

import logging
import os

from .massive import MassiveSource
from .service import MarketDataService
from .simulator import SimulatedSource
from .source import MarketDataSource
from .types import PricePoint, Quote, StreamStatus, Tick, iso_z

log = logging.getLogger(__name__)

__all__ = [
    "MarketDataService", "MarketDataSource", "build_source",
    "PricePoint", "Quote", "StreamStatus", "Tick", "iso_z",
]


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("market: %s=%r is not a number — using %s", name, raw, default)
        return default


def _env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("market: %s=%r is not an integer — ignoring", name, raw)
        return None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"} if raw else default


def build_source() -> MarketDataSource:
    """Env-var driven selection — PLAN.md §5.

    Set and non-empty after stripping -> Massive. Anything else -> the simulator.
    A key of "", "  ", or the literal placeholder from .env.example must NOT send us
    down the Massive path: a student who copies .env.example and never edits it
    should get the simulator, not a wall of 401s.
    """
    key = (os.getenv("MASSIVE_API_KEY") or "").strip()
    if not key or key.lower().startswith(("your-", "your_", "<")):
        interval = _env_float("SIM_INTERVAL_MS", 500.0) / 1000.0
        log.info("market: using the simulator (interval %.0f ms)", interval * 1000)
        return SimulatedSource(
            seed=_env_int("SIM_SEED"),
            interval=interval,
            vol_scale=_env_float("SIM_VOL_SCALE", 4.0),
            half_life_hours=_env_float("SIM_HALF_LIFE_HOURS", 4.0),
        )
    log.info("market: MASSIVE_API_KEY is set — probing entitlement at startup")
    return MassiveSource(
        api_key=key,
        base_url=(os.getenv("MASSIVE_BASE_URL") or "").strip() or "https://api.massive.com",
        snapshot_interval=_env_float("MASSIVE_SNAPSHOT_INTERVAL_S", 15.0),
        grouped_interval=_env_float("MASSIVE_GROUPED_INTERVAL_S", 60.0),
    )
```

**`SIM_INTERVAL_MS` is passed to `SimulatedSource` once** and flows to both `poll_interval`
and the engine's `dt_seconds` from there (§7.4). That is why the constant `0.5` appears in
exactly one default and is never re-typed.

---

## 13. FastAPI wiring

### 13.1 Lifespan

```python
# backend/app/main.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request

from .market import MarketDataService, build_source


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = MarketDataService(build_source())
    app.state.market = service
    await service.start()
    await refresh_tracked(app)                          # watchlist ∪ held positions
    snapshots = asyncio.create_task(snapshot_loop(app)) # background task 2, PLAN.md §3
    try:
        yield
    finally:
        snapshots.cancel()
        await asyncio.gather(snapshots, return_exceptions=True)
        await service.stop()


app = FastAPI(lifespan=lifespan)


def market(request: Request) -> MarketDataService:
    return request.app.state.market


# ---- ROUTE ORDER MATTERS (PLAN.md §11) --------------------------------
# every /api/* router first ...
app.include_router(market_router)
app.include_router(portfolio_router)
app.include_router(watchlist_router)
app.include_router(chat_router)
# ... and the static export LAST. A catch-all mounted at "/" registered first
# shadows every API route, and the failure looks like "the frontend loads but
# every request returns HTML".
mount_static(app)
```

Exactly **two** background tasks run for the process lifetime (`PLAN.md` §3): the market
producer and the 30 s portfolio snapshot recorder. `MarketDataService` internally also runs
the broadcast task; it is an implementation detail of task 1 and starts and stops with it.

### 13.2 The SSE endpoint

```python
# backend/app/routes/market.py
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..market import MarketDataService
from ..market.types import iso_z

router = APIRouter(prefix="/api")

HEARTBEAT_SECONDS = 15.0


def _sse(data: str, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


@router.get("/stream/prices")
async def stream_prices(request: Request, svc: MarketDataService = Depends(market)):
    """Long-lived SSE stream. PLAN.md §6.

    Wire contract:
      * price ticks are sent as the DEFAULT (unnamed) event, so the browser's
        `EventSource.onmessage` receives them with no extra wiring;
      * stream health is sent as a named `status` event, on connect and on change;
      * `: ping` comments every 15 s keep idle connections alive through proxies.
    """
    async def gen():
        q = svc.subscribe()
        last_status = None
        try:
            while True:
                if svc.status != last_status:
                    last_status = svc.status
                    yield _sse(json.dumps(svc.health), event="status")
                try:
                    batch = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"      # a comment: EventSource ignores it
                    continue
                for tick in batch:
                    yield _sse(json.dumps(tick.to_payload()))
        finally:
            svc.unsubscribe(q)              # runs on client disconnect (task cancelled)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",      # stops proxy buffering from batching frames
        },
    )


@router.get("/history/{ticker}")
async def history(ticker: str, svc: MarketDataService = Depends(market)):
    """Ring-buffer history for seeding charts and sparklines. PLAN.md §8.

    Always 200, even for an untracked ticker: an empty `points` array is the honest
    answer and lets the frontend render a placeholder rather than an error toast.
    """
    symbol = ticker.strip().upper()
    return {
        "ticker": symbol,
        "points": [p.to_payload() for p in svc.history(symbol)],
    }


@router.get("/health")
async def health(svc: MarketDataService = Depends(market)):
    return {"status": "ok", "market": svc.health}
```

**Why the price tick is the unnamed event.** `EventSource.onmessage` fires only for events
with no `event:` line. Naming price ticks would force every consumer to
`addEventListener("price", …)`, and the first frontend bug would be a chart that never
updates because someone used `onmessage`. `status` is named precisely because it *should*
be opt-in.

### 13.3 The tracked-set invariant

`set_tracked()` must be called after **every** mutation of either input. Route it through
one helper so no caller ever assembles the union itself:

```python
# backend/app/market_sync.py
from fastapi import FastAPI

from .db import fetch_all


async def refresh_tracked(app: FastAPI) -> frozenset[str]:
    """Recompute watchlist ∪ {tickers with a non-zero position} and push it down.

    PLAN.md §6 is explicit: without the union, removing a HELD ticker from the
    watchlist freezes its price and silently corrupts the positions table, the
    heatmap and total portfolio value. The bug is invisible until someone does
    exactly that, which is why this is one function and not four call sites.
    """
    rows = await fetch_all(
        """
        SELECT ticker FROM watchlist  WHERE user_id = ?
        UNION
        SELECT ticker FROM positions  WHERE user_id = ? AND quantity > 0
        """,
        ("default", "default"),
    )
    tracked = frozenset(r["ticker"] for r in rows)
    app.state.market.set_tracked(tracked)
    return tracked
```

Call it from every one of these, with no exceptions:

| Trigger | Why |
|---|---|
| `POST /api/watchlist` | the new ticker needs a price before its row can render |
| `DELETE /api/watchlist/{t}` | shrinks the set — *unless* a position is still held |
| `POST /api/portfolio/trade` | a buy can introduce a ticker that was never watched; a full sell removes one |
| `POST /api/portfolio/reset` | back to the default ten |
| LLM auto-executed trades and watchlist changes | same paths, same requirement |
| app startup, in `lifespan` | the database already has state from a previous run |

### 13.4 Prices and trades

```python
@router.post("/portfolio/trade")
async def trade(req: TradeRequest, request: Request, svc: MarketDataService = Depends(market)):
    price = svc.price(req.ticker)
    if price is None:
        # Honest, and recoverable: the simulator fills within 500 ms of the ticker
        # entering the tracked set; Massive can take a full poll interval.
        raise HTTPException(
            400, f"No price available for {req.ticker} yet — try again in a moment"
        )
    result = await execute_trade(req, price)      # portfolio agent owns this
    await refresh_tracked(request.app)            # a buy may add, a full sell may remove
    return result
```

`svc.price()` returning `None` is the **only** correct signal for "not priced yet". Never
substitute `0.0` — it would let a buy of a $0.00 stock through and mint infinite shares.

### 13.5 A newly added ticker

`set_tracked()` does no I/O, so the timeline for a brand-new ticker is:

| Source | Price available after |
|---|---|
| Simulator | **immediately** — `prime()` returns the seed price with no RNG consumed (§7.4) |
| Massive SNAPSHOT | up to 15 s (the next poll) |
| Massive GROUPED | up to 60 s — and only if the symbol traded that day |

Until a price exists, `svc.price()` returns `None`, `/api/history` returns `points: []`, and
**the watchlist row must render `—`, not `$0.00`.** That is a small, honest gap. Do not
paper over it by minting a fake price for a real-data source — `prime()` is safe for the
simulator only because a simulated ticker's seed price genuinely *is* its opening price.

---

## 14. The wire contract

### 14.1 SSE frames

A connect, then one broadcast, then an idle period:

```
event: status
data: {"source":"simulator","status":"connected","reason":null,"tracked":["AAPL","GOOGL"],"poll_interval":0.5,"subscribers":1}

data: {"ticker":"AAPL","price":191.24,"prev_price":191.19,"open":190.02,"change":1.22,"change_pct":0.6421,"direction":"up","ts":"2026-08-26T14:03:07.412Z"}

data: {"ticker":"GOOGL","price":174.88,"prev_price":174.95,"open":175.0,"change":-0.12,"change_pct":-0.0686,"direction":"down","ts":"2026-08-26T14:03:07.412Z"}

: ping

```

A fallback mid-session emits a fresh `status` frame the client can act on:

```
event: status
data: {"source":"simulator","status":"degraded","reason":"upstream unavailable, using the simulator (429: rate limited)","tracked":["AAPL"],"poll_interval":0.5,"subscribers":1}
```

### 14.2 The browser side

```ts
const es = new EventSource("/api/stream/prices");

es.onmessage = (e) => applyTick(JSON.parse(e.data) as Tick);   // price ticks

es.addEventListener("status", (e) => {
  const h = JSON.parse((e as MessageEvent).data);
  setDot(h.status === "connected" ? "green" : "yellow", h.reason);
});

es.onerror = () => setDot("red");   // EventSource retries on its own; do not reconnect manually
```

Mapping onto `PLAN.md` §10's connection dot:

| Dot | When |
|---|---|
| green | last `status` frame said `connected` |
| yellow | last `status` frame said `degraded`, **or** `onerror` fired and a retry is pending |
| red | `es.readyState === CLOSED` |

### 14.3 `GET /api/history/{ticker}`

```json
{
  "ticker": "AAPL",
  "points": [
    {"ts": "2026-08-26T14:02:37.905Z", "price": 190.02},
    {"ts": "2026-08-26T14:02:38.407Z", "price": 190.05}
  ]
}
```

Up to 600 points, oldest first, chronological. An untracked ticker returns
`{"ticker": "ZZZZ", "points": []}` with a `200`.

### 14.4 `GET /api/health`

```json
{
  "status": "ok",
  "market": {
    "source": "massive",
    "status": "degraded",
    "reason": "end-of-day data (free Massive tier)",
    "tracked": ["AAPL", "AMZN", "GOOGL", "JPM", "META", "MSFT", "NFLX", "NVDA", "TSLA", "V"],
    "poll_interval": 60.0,
    "subscribers": 1
  }
}
```

The Docker `HEALTHCHECK` (`PLAN.md` §11) keys off the outer `"status": "ok"` only — a
`degraded` **market** is a healthy **container**, and failing the healthcheck because a
student's free key returns end-of-day data would make `docker ps` lie.

---

## 15. Budgets, and what to log

### 15.1 Work per second

At the default settings with the watchlist at its cap (30 tickers, `PLAN.md` §8):

| Quantity | Value |
|---|---|
| Simulator steps | 2/s |
| Quotes produced | 60/s |
| RNG draws per tick | `1 + |sectors| + |tickers|` ≤ 38 |
| `PriceCache.apply` calls | 60/s |
| SSE frames per client | ≤ 60/s (one per changed ticker per 500 ms broadcast) |
| Ring memory | 30 × 600 × ~130 B ≈ **2.3 MB** |
| Massive SNAPSHOT | 4 requests/min, a few KB each |
| Massive GROUPED | 1 request/min, **1–3 MB** of JSON parsed and 99.7% discarded |

The only line worth watching is the last one: a 1–3 MB `resp.json()` on the event loop
every 60 s is a ~20–60 ms stall. That is acceptable at one per minute and is why GROUPED
mode polls at 60 s rather than 12 s (its rate-limit floor). If it ever becomes a problem,
`await asyncio.to_thread(json.loads, resp.text)` is the one-line fix — measure first.

`PLAN.md` §3 requires that database work is moved off the event loop with
`asyncio.to_thread`. **Nothing in this module touches the database**, so that rule applies
to `refresh_tracked()` (§13.3) and the snapshot recorder, not here. The GBM step stays on
the loop deliberately (§7.5).

### 15.2 Logging

Quiet at `INFO` during steady state — a log line per tick would produce 172,800 lines an
hour and hide everything that matters.

| Level | Event |
|---|---|
| `INFO` | source selected at startup, with interval |
| `INFO` | Massive mode resolved (`SNAPSHOT` / `GROUPED` + reason) |
| `INFO` | simulated event fired: `simulated event: NVDA +3.83%` |
| `INFO` | slow SSE subscriber dropped |
| `INFO` | GROUPED trading date rolled over |
| `WARNING` | a `fetch()` failure, with the attempt count |
| `WARNING` | a 429 and the new poll interval |
| `WARNING` | Massive switched SNAPSHOT → GROUPED mid-flight |
| `ERROR` | fallback to the simulator (once per process) |
| `DEBUG` | snapshot rows missing for requested tickers |

**Always log Massive's `request_id`** — `_get()` already threads it into every exception
message. It is the first thing Massive support asks for, and it is in the `x-request-id`
header of every response, success or failure.

**Never log the API key.** The Bearer header keeps it out of URLs, which is exactly why
§8.3 uses the header rather than `?apiKey=` even though the SDK examples use the latter.

---

## 16. Deviations and corrections

Recorded so nobody "fixes" them back, and so the three contract documents and the code can
be reconciled by anyone reading either.

### 16.1 Carried over from `MARKET_INTERFACE.md` §10 (unchanged, restated)

1. **The broadcast emits only tickers that changed**, not every tracked ticker every 500 ms.
   Under the simulator this is identical — everything changes every tick. Under a free-tier
   key it is the difference between 60 identical frames a second and none. The 15 s comment
   heartbeat in §13.2 exists because of this.
2. **`degraded` covers "working but end-of-day"**, not only "upstream failed". `PLAN.md`
   §10 defines yellow as "reconnecting *or* degraded"; this extends the second case to a
   healthy free-tier key. It is the correct signal — the dot is telling the user their
   prices are not live, which is true.

### 16.2 Corrections to the contract documents

| # | Document | Issue | Resolution here |
|---|---|---|---|
| C1 | `MARKET_SIMULATOR.md` §10 | Proposes `spec_for("PYPL").price == 178.51` as the cross-process determinism test. PYPL is in `SEED_PRICES` at **$68.00**, so the table wins and this assertion fails against correct code. 178.51 is what `_synthesise` alone produces. | Assert on a symbol genuinely absent from the table: `spec_for("SNOW").price == 348.97`. Verified. §5 carries the full reference table. |
| C2 | `MARKET_SIMULATOR.md` | §3 specifies `β̂_i`, "the ticker's beta normalised so the mean loading is 1", but the §8 code sketch uses the raw `st.spec.beta`. | §7.4 implements the normalisation the prose specifies — against a **module constant** `MEAN_BETA = 1.08` (the mean over `SEED_PRICES`), not the mean over the current watchlist. A per-watchlist mean makes a ticker's volatility depend on what else the user happens to be watching: measured, NVDA's realised sd moves **18.8%** between a high-beta and a mixed watchlist, versus **0.53%** with the constant. |
| C3 | `MASSIVE_API.md` §7 / `MARKET_INTERFACE.md` §5 | `grouped_daily` stamps quotes with `datetime.now()`, so re-polling an unchanged EOD bar produces a *newer* timestamp and defeats `PriceCache.apply`'s repeat detection (`price == st.price and ts <= updated_at`). The free-tier path would then emit a `flat` tick every 60 s and fill the ring with duplicates — the exact behaviour `MARKET_INTERFACE.md` §5 says it avoids. | §8.3 stamps grouped quotes with the bar's own `t` (milliseconds). Re-polls are byte-identical and dedupe correctly. |
| C4 | `MASSIVE_API.md` §7 | Declares a second `Quote` dataclass inside `massive.py`, duplicating `types.Quote` with an incompatible signature (`session_open` required and positioned differently). | One `Quote`, in `types.py`. `massive.py` imports it. |
| C5 | `MASSIVE_API.md` §7 | `EASTERN = timezone(timedelta(hours=-5))` is EST and is wrong by an hour during daylight saving. | `zoneinfo.ZoneInfo("America/New_York")` — stdlib, no dependency, correct across DST. |
| C6 | `MARKET_INTERFACE.md` §8 | Says a 401 out of `MassiveSource.start()` "propagates; the caller catches it" without naming the caller. | Caught in `MarketDataService.start()` (§10), covering every start-time failure in one place. |
| C7 | `MARKET_INTERFACE.md` §8 | `SimulatedSource.set_tickers` calls `engine.ensure()` but never `engine.forget()`, so engine state grows without bound while `PriceCache.evict()` shrinks — the two drift apart. | §7.4 calls both, so the engine and the cache always agree on what exists. |
| C8 | `MASSIVE_API.md` §4.1 | Nothing re-resolves the cached trading date, so a container left running overnight serves yesterday's close indefinitely. | `_refresh_date_if_stale()` re-resolves at most once per ET calendar day (§8.4). |
| C9 | `MARKET_SIMULATOR.md` §10 | The statistical tests (`test_gbm_log_returns_match_analytic_distribution`, `test_correlation_matches_the_factor_weights`) are specified at default settings. **They fail against correct code**: jumps contribute 87–99% of per-tick variance (§7.2.1), so the sample variance comes out ~130× the closed form and measured AAPL/MSFT correlation is **0.004**, not 0.40. Verified both ways. | `GbmEngine` takes `event_prob`; all statistical tests set `event_prob=0.0` alongside `half_life_hours=0.0`. With both, measured variance is 5.714e-9 against a closed form of 5.732e-9, and correlations land within 0.005 of the closed form (§17.2). |
| C10 | `MARKET_SIMULATOR.md` §3 | Claims "the weights are variance shares summing to 1 and `Z_i` stays unit-variance". That is only true when `β̂_i = 1`: in general `Var(Z_i) = w_m·β̂_i² + w_s + w_idio`, so a β-1.6 name runs ~18% hotter than its table `volatility`. | §7.4 divides the factor blend by `n_i = sqrt(w_m·β̂_i² + w_s + w_idio)`, making the document's own claim true. Measured realised sd ÷ theoretical: **1.002 / 1.003 / 0.999** for AAPL / NVDA / KO. Correlations become beta-dependent around the 0.40 / 0.25 headline; §7.3 tabulates the real values. |
| C11 | `MARKET_SIMULATOR.md` §10 | `test_mean_reversion_bounds_a_long_run` asserts every ticker stays within ±25% of its seed after 8 hours **at default settings**. It does not: each jump moves the anchor by half the jump, so the anchor random-walks (~9.4% sd over 8 h) and the OU pull bounds only the deviation *from the anchor*. Measured worst case 52.6%. | The bound test runs with `event_prob=0.0`, where ±25% holds comfortably (measured max 17.7%). A separate test documents the unbounded behaviour with jumps on rather than asserting it away. |
| C12 | `MARKET_SIMULATOR.md` §7 | `_synthesise` returns unrounded floats, so the top of each range lands one ULP over the bound — `0.20 + 4000/10000 == 0.6000000000000001` — and a range assertion written the obvious way fails on 1 symbol in 4,001. | §5 rounds every synthesised field. |

### 16.3 Additions

| # | Addition | Rationale |
|---|---|---|
| A1 | `MarketDataSource.prime()`, defaulting to `[]` | Removes the 500 ms "no price" window for a ticker just added to the watchlist, without the service ever branching on source type. `SimulatedSource` returns the seed price, consuming **no** randomness so determinism (§7.6) holds; `MassiveSource` keeps the default because a real-data source must not invent a price. |
| A2 | `MassiveRateLimitError` + monotonic `_raise_interval()` | `MASSIVE_API.md` §6: "a 429 means the interval is wrong, not that this one request was unlucky." Lowering the interval again on the next success would oscillate straight back into a 429. |
| A3 | `types.utcnow()` | One clock for three modules, so a test that needs a frozen clock patches one name instead of monkey-patching `datetime` in three places. |
| A4 | `fallback_factory` constructor argument on `MarketDataService` | Lets the fallback test assert on the swap without a real GBM engine, and keeps `_fall_back` free of `isinstance` gymnastics. |
| A5 | Named `status` SSE event; price ticks stay unnamed | `EventSource.onmessage` only fires for unnamed events. Naming the price event would guarantee a "chart never updates" bug on the frontend's first day. |
| A6 | `MASSIVE_BACKFILL`, **off by default** | Backfilling ten tickers on a free key is ten calls against a 5/min budget — a guaranteed 429 storm at startup. A paid-key nicety, not a default. |
| A7 | `GbmEngine(event_prob=...)` | The statistical tests cannot be written at all without it (C9). Defaults to `EVENT_PROB_PER_TICK`, so production behaviour is unchanged; it is a test seam, not a knob, and deliberately has no environment variable. |

---

## 17. Tests

Dependencies: `pytest`, `pytest-asyncio`, `respx` (dev group). **Never hit
`api.massive.com` from a test.**

### 17.1 The shared source suite

The interface earns its keep only if both implementations are held to the same suite.

```python
# backend/tests/market/test_source_contract.py
import pytest
import respx
from httpx import Response

from app.market.massive import MassiveSource
from app.market.simulator import SimulatedSource

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=["simulator", "massive"])
async def source(request):
    if request.param == "simulator":
        src = SimulatedSource(seed=42)
        await src.start()
        yield src
        await src.aclose()
        return
    with respx.mock(base_url="https://api.massive.com") as mock:
        mock.get(url__startswith="/v2/snapshot").mock(
            return_value=Response(200, json=load_fixture("snapshot_aapl_msft.json"))
        )
        src = MassiveSource(api_key="test-key")
        await src.start()
        yield src
        await src.aclose()


async def test_fetch_returns_quotes_only_for_tracked_tickers(source):
    source.set_tickers(frozenset({"AAPL", "MSFT"}))
    quotes = await source.fetch()
    assert {q.ticker for q in quotes} <= {"AAPL", "MSFT"}
    assert all(q.price > 0 for q in quotes)
    assert all(q.ts.tzinfo is not None for q in quotes)     # always tz-aware UTC


async def test_empty_ticker_set_yields_no_quotes(source):
    source.set_tickers(frozenset())
    assert await source.fetch() == []


async def test_set_tickers_is_idempotent_and_io_free(source):
    source.set_tickers(frozenset({"AAPL"}))
    source.set_tickers(frozenset({"AAPL"}))          # must not raise, must not await
    assert await source.fetch() is not None


async def test_aclose_is_idempotent(source):
    await source.aclose()
    await source.aclose()


async def test_untracked_ticker_is_never_quoted(source):
    source.set_tickers(frozenset({"AAPL"}))
    assert all(q.ticker == "AAPL" for q in await source.fetch())
```

### 17.2 Simulator

**Statistical.** Every one of these runs with **`half_life_hours=0.0` AND
`event_prob=0.0`**. Both are required: `kappa=0` makes the analytic result exact, and
disabling jumps isolates the diffusion — with jumps on, the sample variance is ~130× the
closed form and measured correlation collapses to 0.004 (§7.2.1). A test written without
both flags fails against correct code.

```python
def test_gbm_log_returns_match_the_analytic_distribution():
    """With kappa=0 and no jumps, log(S_t+1/S_t) ~ N((mu - sigma^2/2)*dt, sigma^2*dt)."""
    eng = GbmEngine(seed=7, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    tickers = frozenset({"AAPL"})
    eng.step(tickers)

    # Read the CONTINUOUS log path, not the emitted price: quotes are rounded to
    # cents (§7.5), and on a $190 stock a 0.0102% per-tick move is ~2 cents, so
    # differencing rounded prices measures the rounding, not the process.
    state = eng._states["AAPL"]
    rets, prev = [], state.log_price
    for _ in range(200_000):
        eng.step(tickers)
        rets.append(state.log_price - prev)
        prev = state.log_price

    spec = spec_for("AAPL")
    dt = 0.5 / TRADING_YEAR_SECONDS
    exp_mean = (spec.drift - 0.5 * spec.volatility**2) * dt
    exp_var = spec.volatility**2 * dt

    mean, var = statistics.fmean(rets), statistics.pvariance(rets)
    assert abs(mean - exp_mean) < 3 * math.sqrt(exp_var / len(rets))
    assert var == pytest.approx(exp_var, rel=0.05)     # measured: 5.714e-9 vs 5.732e-9
```

```python
def test_correlation_matches_the_closed_form():
    """rho_ij = (w_m*bh_i*bh_j + w_s[same sector]) / (n_i * n_j)  -- §7.3."""
    eng = GbmEngine(seed=11, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    paths = collect_log_returns(eng, {"AAPL", "MSFT", "JPM", "NVDA", "KO"}, n=80_000)
    for a, b, expected in [
        ("AAPL", "MSFT", 0.377),      # tech / tech
        ("NVDA", "MSFT", 0.426),
        ("AAPL", "NVDA", 0.447),
        ("AAPL", "JPM", 0.212),       # tech / finance
        ("AAPL", "KO", 0.138),        # tech / consumer
    ]:
        assert correlation(paths[a], paths[b]) == pytest.approx(expected, abs=0.03)
```

| Test | Asserts | Flags |
|---|---|---|
| `test_no_drift_no_vol_is_a_flat_line` | μ = σ = 0 ⇒ the price never changes over 2,000 ticks. Catches sign and `dt` errors instantly, with no statistics | `event_prob=0.0` — a jump fires in ~22% of 500-tick runs and this test would flake |
| `test_z_has_unit_variance_for_every_beta` | realised sd ÷ `sigma*sqrt(dt)` ∈ [0.99, 1.01] for AAPL (β 1.05), NVDA (β 1.55) and KO (β 0.55). The C10 guard | `event_prob=0.0`, `kappa=0` |
| `test_a_tickers_volatility_does_not_depend_on_the_watchlist` | NVDA's realised sd in `{NVDA,TSLA,PLTR}` is within 2% of its sd in `{NVDA,KO,JNJ}`. Measured: 0.53% apart. The C2 guard — under a per-watchlist mean beta this is 18.8% and fails | `event_prob=0.0`, `kappa=0` |
| `test_mean_reversion_bounds_a_long_run` | 8 simulated hours: every ticker within **±25%** of its seed. Measured max over 8 seeds: 17.7% | **`event_prob=0.0`** — see the note below |
| `test_jumps_make_the_long_run_unbounded` | the same 8 hours **with** jumps exceeds ±25% on some seeds (measured max 52.6%), documenting that the anchor random-walks | defaults |
| `test_events_fire_at_the_expected_rate` | with `EVENT_PROB_PER_TICK` forced to 0.5: jumps occur, each within 2–5% |
| `test_event_moves_the_anchor_halfway` | the anchor delta is exactly half the price jump |
| `test_prices_are_rounded_to_cents_but_state_is_not` | emitted prices have ≤2 decimals while consecutive internal `log_price` values differ by less than the rounding step |
| `test_new_ticker_added_mid_run_starts_at_its_seed_price` | and its `session_open` equals that seed, so its change column starts at 0.00% |
| `test_step_with_an_empty_set_draws_no_rng` | two engines, same seed, one stepped empty 100× first, then both stepped with `{"AAPL"}` ⇒ identical output |
| `test_forget_releases_state` | shrinking the set drops engine state; re-adding restarts at the seed |
| `test_prime_consumes_no_randomness` | `prime()` between two `step()`s leaves the sequence identical to no `prime()` at all — the A1 guard | defaults |

> **On the ±25% reversion bound.** `MARKET_SIMULATOR.md` §10 states it for the default
> configuration. It does not hold there: each jump moves the **anchor** by half the jump
> (§5), so over 8 hours a ticker takes ~29 jumps and the anchor itself random-walks with a
> standard deviation of ~9.4%. The OU pull bounds the deviation *from the anchor*, not from
> the seed price. Measured worst-case deviation after 8 simulated hours, over 10 tickers:
> **17.7% with jumps off** (8 seeds) versus **52.6% with jumps on** (12 seeds). So the
> bound test isolates κ with `event_prob=0.0`, and the unbounded behaviour gets its own
> test rather than being asserted away. This is by design and not a defect: news that
> permanently re-rates a stock is exactly what §5 is modelling.

**Determinism**

```python
def test_same_seed_same_sequence():
    a, b = GbmEngine(seed=42), GbmEngine(seed=42)
    ts = frozenset({"AAPL", "MSFT", "JPM"})
    assert [a.step(ts) for _ in range(1000)] == [b.step(ts) for _ in range(1000)]


def test_different_seed_different_sequence():
    """The complement, so the test above cannot pass by producing constants."""
    a, b = GbmEngine(seed=1), GbmEngine(seed=2)
    ts = frozenset({"AAPL"})
    assert [a.step(ts) for _ in range(100)] != [b.step(ts) for _ in range(100)]


def test_seed_price_is_stable_across_processes(tmp_path):
    """Catches someone swapping hashlib.sha256 for the builtin hash().

    Runs in a SUBPROCESS with a different PYTHONHASHSEED: builtin hash() is salted
    per process, so it would produce a different price on every restart and break
    E2E reproducibility outright. SNOW is used, not PYPL — PYPL is in the table
    at $68.00 (see §16.2 C1).
    """
    code = "from app.market.seeds import spec_for; print(spec_for('SNOW').price)"
    for hashseed in ("0", "1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": hashseed},
            capture_output=True, text=True, check=True,
        )
        assert out.stdout.strip() == "348.97"
```

**Seeds**

| Test | Asserts |
|---|---|
| `test_known_tickers_use_the_table` | `spec_for("AAPL").price == 190.00`; the table wins over the hash for `AMD` ($160.00, not $104.85) |
| `test_unknown_ticker_lands_in_range` | over all 17,576 three-letter symbols, **calling `_synthesise` directly**: price ∈ [20.00, 500.00], σ ∈ [0.20, 0.60], μ ∈ [−0.05, 0.20], β ∈ [0.70, 1.60]. Sweeping `spec_for` instead trips over the table's out-of-range entries (LLY $820, SPY $640) — see §5 |
| `test_synthesised_values_are_rounded` | the top of each range is exactly on the bound, not one ULP over it (`0.20 + 4000/10000 == 0.6000000000000001` without the rounding in §5) |
| `test_unknown_ticker_is_never_rejected` | `spec_for` raises for no input matching `^[A-Z]{1,5}$` — including 1-letter (`"A"`) and 5-letter (`"ABCDE"`) symbols |

### 17.3 Massive

All with `respx`; fixtures are trimmed real response bodies committed under
`backend/tests/fixtures/massive/`.

| Test | Asserts |
|---|---|
| `test_403_on_the_probe_selects_grouped_mode` | `start()` with a 403 ⇒ `_mode == "grouped"`, `poll_interval == 60`, `degraded_reason` set |
| `test_401_propagates_out_of_start` | `MassiveAuthError` escapes `start()` |
| `test_403_mid_flight_switches_mode_without_raising` | SNAPSHOT `fetch()` returning 403 ⇒ returns `[]`, mode flips, **no exception** (it must not count as a failure) |
| `test_429_raises_the_interval_and_re_raises` | interval 15 → 22.5 **and** `MassiveRateLimitError` propagates |
| `test_429_interval_is_monotonic` | a success after a 429 does not lower it back |
| `test_nanosecond_and_millisecond_timestamps_both_parse` | `updated` (ns) and `day.t` (ms) both land in a sane year — the year-52,000 guard |
| `test_zero_filled_day_falls_through_to_prev_close` | the 3:30–4:00 AM ET cleared-snapshot window yields `prevDay.c`, never `$0.00` |
| `test_snapshot_row_without_any_price_returns_none` | a row with no `lastTrade`/`min`/`day`/`prevDay` is skipped, not zero-filled |
| `test_missing_tickers_are_absent_not_errors` | requesting `{"AAPL","ZZZZ"}` with only AAPL in the body ⇒ one quote, no raise |
| `test_grouped_keeps_only_tracked_tickers` | a 10k-row body ⇒ exactly the tracked set |
| `test_grouped_quote_timestamp_is_the_bar_not_now` | the C3 regression guard: two polls of the same body produce equal `Quote` objects |
| `test_trading_date_walks_back_over_a_weekend` | three empty responses then a populated one ⇒ the fourth date, 4 requests total |
| `test_trading_date_refreshes_once_per_et_day` | two fetches on the same ET day ⇒ one resolution; across a rollover ⇒ two |
| `test_bearer_header_is_used_and_the_key_is_never_in_a_url` | assert on the recorded request: `Authorization` present, `apiKey` absent from `str(request.url)` |

### 17.4 Cache

| Test | Asserts |
|---|---|
| `test_first_quote_is_flat_and_pins_the_session_open` | `direction == "flat"`, `prev_price == price`, `open == session_open` |
| `test_change_pct_is_measured_against_open_and_direction_against_prev` | open 100 → 110 → 109: `direction == "down"` while `change_pct == +9.0` |
| `test_a_repeated_identical_quote_returns_none` | and does **not** grow the ring |
| `test_session_open_is_not_overwritten_by_a_none` | a `Quote(session_open=None)` leaves an established open intact |
| `test_ring_buffer_caps_at_600_and_drops_oldest_first` | 700 quotes ⇒ 600 points, the first is the 101st quote |
| `test_evict_drops_untracked_state_and_its_ring` | |
| `test_history_of_an_unknown_ticker_is_empty` | returns `[]`, does not raise |
| `test_zero_open_does_not_divide_by_zero` | `change_pct == 0.0` |

### 17.5 Service

```python
class FlakySource(MarketDataSource):
    """Fails on demand. The failure ladder is policy, so it is tested directly."""

    name = "flaky"

    def __init__(self, fail_times: int) -> None:
        self.poll_interval = 0.01
        self.degraded_reason = None
        self._left = fail_times
        self.closed = False

    def set_tickers(self, tickers): self._tickers = tickers
    async def aclose(self): self.closed = True

    async def fetch(self):
        if self._left > 0:
            self._left -= 1
            raise RuntimeError("upstream boom")
        return [Quote("AAPL", 100.0, utcnow())]


async def test_three_consecutive_failures_fall_back_to_the_simulator():
    svc = MarketDataService(FlakySource(fail_times=99))
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: svc.health["source"] == "simulator", timeout=5)
    assert svc.status is StreamStatus.DEGRADED
    assert "upstream unavailable" in svc.health["reason"]
    await svc.stop()
```

| Test | Asserts |
|---|---|
| `test_two_failures_then_success_does_not_fall_back` | the counter resets; source is unchanged; status back to `connected` |
| `test_fallback_closes_the_old_source` | `old.closed is True` |
| `test_fallback_happens_once` | a simulator that fails does not recurse |
| `test_a_source_that_fails_to_start_falls_back` | `start()` raising ⇒ simulator, `degraded`, producer still running |
| `test_a_full_subscriber_is_dropped_without_stalling_the_producer` | fill a queue to 64, broadcast, assert it is unsubscribed and the producer keeps ticking |
| `test_subscribe_primes_from_the_cache_snapshot` | a client connecting mid-session gets the current state immediately |
| `test_set_tracked_is_idempotent` | equal sets ⇒ no `set_tickers`/`evict` call (spy on the source) |
| `test_set_tracked_primes_new_simulator_tickers` | `svc.price("PYPL")` is non-None **synchronously** after `set_tracked` |
| `test_set_tracked_does_not_prime_massive_tickers` | `svc.price()` stays `None` until a poll — A1 must not leak into real data |
| `test_stop_cancels_both_tasks_and_sets_disconnected` | |
| `test_degraded_reason_from_the_source_marks_the_stream_degraded` | a healthy source with `degraded_reason` set ⇒ yellow, no failure counted |

### 17.6 Routes

| Test | Asserts |
|---|---|
| `test_history_returns_iso_z_millisecond_timestamps` | every `ts` matches `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$` |
| `test_history_of_an_untracked_ticker_is_200_with_no_points` | not a 404 |
| `test_sse_emits_a_status_frame_first_then_ticks` | parse the first two frames of the stream |
| `test_sse_sends_a_heartbeat_when_idle` | with no producer, a `: ping` arrives within ~15 s (patch `HEARTBEAT_SECONDS` down) |
| `test_sse_unsubscribes_on_client_disconnect` | `svc.health["subscribers"]` returns to 0 |
| `test_trade_against_an_unpriced_ticker_is_400` | and the `detail` names the ticker |
| `test_health_reports_degraded_market_as_a_healthy_container` | outer `"status": "ok"` |

### 17.7 E2E hooks (`test/`, `LLM_MOCK=true`)

`PLAN.md` §12 scenarios that land on this module — all of them assume `SIM_SEED=42` and the
simulator:

- **Fresh start** — the ten default tickers appear with non-zero prices within a few seconds
- **Prices stream** — a watchlist row's price text changes within 2 s of load
- **Reload persistence** — `GET /api/history/{ticker}` seeds a populated chart, so a reload
  never shows an empty one
- **SSE resilience** — kill the connection, assert `EventSource` reconnects and prices resume
- **Held-but-unwatched ticker** — buy a ticker, remove it from the watchlist, assert its
  price still streams and its position stays live. **This is the §13.3 union invariant, and
  it is the single most valuable E2E test in this document** — the bug it catches is
  invisible in every other scenario.

---

## 18. Implementation order

Each step is independently testable; do not start the next until the previous one's tests
pass.

1. **`types.py`** — plus a doctest on `iso_z`. Nothing else compiles without it.
2. **`seeds.py`** — the full table and `_synthesise`. Run the range sweep and the
   subprocess determinism test now; getting `hash()` vs `sha256` wrong here poisons
   everything downstream.
3. **`source.py`** — the ABC. No logic, but it is what the next two files are checked against.
4. **`cache.py`** — pure, synchronous, no I/O, no asyncio. The tick-derivation tests in
   §17.4 are the highest-value tests in the module; write them here.
5. **`simulator.py`** — `GbmEngine` first, with the statistical tests at
   `half_life_hours=0.0, event_prob=0.0` (both flags — §7.2.1), then mean reversion, then
   jumps, then the `SimulatedSource` wrapper.
6. **`service.py`** — with `FlakySource`. The failure ladder is policy; test it before any
   real source is wired in.
7. **`__init__.py`** — `build_source()` and the env parsing.
8. **FastAPI wiring** — lifespan, SSE, `/api/history`, `/api/health`. At this point the app
   streams prices end to end on the default path, which is what most users will ever run.
9. **`refresh_tracked()`** and its call sites — every one in the §13.3 table. Write the
   held-but-unwatched E2E test here.
10. **`massive.py`** — client, then source, entirely against `respx` fixtures.
11. **`scripts/check_massive.py`** — and only now try a real key.
12. **Optional backfill** (§8.6), if a paid key is available to test it against.

Steps 1–9 deliver everything `PLAN.md` promises on the default path. Steps 10–12 are the
optional `MASSIVE_API_KEY` path, and nothing in 1–9 may depend on them.

### Definition of done

- [ ] The shared contract suite (§17.1) passes against **both** sources
- [ ] `SIM_SEED=42` produces byte-identical price sequences across two runs
- [ ] `spec_for` never raises for any symbol matching `^[A-Z]{1,5}$`
- [ ] Every statistical test sets **both** `half_life_hours=0.0` and `event_prob=0.0`, and
      the closed-form correlation and variance assertions pass (§17.2)
- [ ] A ticker's realised volatility does not change when an unrelated ticker joins the
      watchlist
- [ ] Three consecutive `fetch()` failures swap in the simulator and turn the dot yellow
- [ ] A free-tier 403 selects GROUPED mode without ever counting a failure
- [ ] The SSE stream survives a source failure, a slow client, and a 15 s idle period
- [ ] `direction` compares to `prev_price`; `change_pct` compares to `session_open`; both
      asserted independently
- [ ] `refresh_tracked()` is called from all six sites in §13.3, and the held-but-unwatched
      E2E test passes
- [ ] No module outside `app.market` imports anything but `service` and `types`
- [ ] No test reaches `api.massive.com`

---

## 19. How this document was verified

Every code block in §4–§12 was extracted, assembled into a package and executed before this
document was committed. That is why the numbers above are stated flatly rather than hedged —
and why §16 has twelve entries rather than none.

**What was executed and passed** (61 assertions):

| Area | Checked |
|---|---|
| `types.py` | `iso_z` produces exactly `2026-08-26T14:03:07.412Z` |
| `seeds.py` | all 17,576 three-letter symbols land in every documented range; `SNOW → 348.97`; the table beats the hash for `AMD`; `MEAN_BETA` matches the table |
| Determinism | 1,000 ticks × 2 engines at seed 42 byte-identical; different seeds differ; an empty `step()` draws no RNG; `prime()` draws no RNG |
| GBM | μ = σ = 0 is flat over 2,000 ticks; sample variance **5.7147e-9** against a closed form of **5.7319e-9**; sample mean within 3 SE |
| Correlation | five pairs against the closed form (§7.3), all within 0.005; realised sd ÷ theoretical = 1.002 / 1.003 / 0.999 across β 0.55–1.55; a ticker's vol shifts **0.53%** between watchlists |
| Mean reversion | 8 simulated hours × 8 seeds: max deviation 17.7% with jumps off, 52.6% with jumps on |
| `cache.py` | first tick flat and open pinned; `direction` vs `prev_price` while `change_pct` vs `open`; repeat ⇒ `None` and no ring growth; ring caps at 600 dropping oldest; `evict` clears the ring; zero open does not divide by zero |
| `service.py` | 3 failures ⇒ simulator + `degraded` + old source closed + prices resume; 2 failures then success keeps the source and returns to `connected`; a `start()` that raises falls back; `prime()` gives a simulator ticker a price synchronously and is a no-op for other sources; a full subscriber is dropped while the producer keeps ticking; `stop()` ⇒ `disconnected` |
| `massive.py` | nanosecond and millisecond fields both parse to 2020, not year 52,000; `lastTrade → min → day → prevDay` fallback; the zero-filled 3:30 AM window yields `prevDay.c`; a priceless row returns `None`; grouped filtering keeps only tracked tickers; **two grouped polls produce identical `Quote`s, dedupe in the cache, and leave the ring at one point** (the C3 fix, end to end) |

**What was *not* executed, and the residual risk:**

- **No request ever reached `api.massive.com`.** The client's HTTP layer (`_get`, status-code
  branching, the Bearer header, `latest_trading_date`'s walk-back) is checked by reading
  against `MASSIVE_API.md`, not by running. Fixtures and `respx` cover it at implementation
  time (§17.3), and `scripts/check_massive.py` (§8.7) is the first thing to run with a real
  key. `MASSIVE_API.md` §10 already flags the 403 *body* shape as reported-not-verified,
  which is why §8.3 branches on the status code alone.
- **The FastAPI wiring in §13 was not run** — there is no `backend/` yet. The SSE generator,
  the heartbeat and the disconnect path are reasoned, not measured.
- **Seed prices are plausible, not accurate.** They approximate mid-2026 levels; §5 says why
  recognisability is the goal.
- **`MEAN_BETA = 1.08` is pinned as a literal** rather than computed from `SEED_PRICES` at
  import time. That is deliberate — it must not shift when someone adds a ticker to the
  table, or every documented correlation in §7.3 silently changes. Re-derive it consciously
  if the table's beta distribution ever changes materially, and update §7.3's table with it.
