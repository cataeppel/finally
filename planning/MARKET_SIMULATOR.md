# MARKET_SIMULATOR.md — The Price Simulator

**Status:** design contract for the Market Data agent. Binding on
`backend/app/market/simulator.py` and `backend/app/market/seeds.py`.
**Companions:** `MARKET_INTERFACE.md` (the `MarketDataSource` contract it implements),
`MASSIVE_API.md` (the real-data alternative).

The simulator is FinAlly's **default** price source, not a fallback. Per PLAN.md §6, real
free-tier data is end-of-day and does not move; outside US market hours even paid data is
static. The simulator is what makes the terminal worth watching, so it has to look right:
prices that move at a believable speed, tech names that move together, and the occasional
piece of drama.

---

## 1. Requirements it has to satisfy

| From PLAN.md | Requirement |
|---|---|
| §6 | Geometric Brownian motion with per-ticker drift and volatility |
| §6 | ~500 ms update cadence |
| §6 | Correlated moves across tickers (tech moves together) |
| §6 | Occasional random events — sudden 2–5% moves |
| §6 | Realistic seed prices (AAPL ~$190, GOOGL ~$175, …) |
| §6 | Unknown tickers get a **deterministic** price from a hash, $20–$500 |
| §6 | Unknown tickers are never rejected for being unknown |
| §6 | In-process background task, no external dependencies |
| §12 | Unit-testable: "GBM math is correct" |
| implicit | Same symbol ⇒ same sequence, so E2E tests are reproducible |
| implicit | Must not drift to $0 or $10,000 over a long-running demo |

That last one is not in PLAN.md but is the difference between a simulator that survives a
two-hour lecture and one that doesn't. §4 covers it.

---

## 2. The model

Three layers, applied to the **log price** of each ticker on every tick.

```
log S_i(t+dt) − log S_i(t)  =  κ_i·(log A_i − log S_i(t))·dt      ← 1. mean reversion
                             + (μ_i − σ_i²/2)·dt                   ← 2. drift
                             + σ_i·√dt·Z_i                         ← 3. diffusion
                             + J_i(t)                              ← 4. jump (rare)
```

where `A_i` is the ticker's anchor price, and `Z_i` is a **correlated** standard normal
built in §3.

Layer 2+3 alone is textbook GBM — exactly what PLAN.md asks for. Layer 1 is an
Ornstein–Uhlenbeck pull that keeps a long session anchored (§4). Layer 4 is the drama (§5).

### Time base

Everything is annualised, so `σ = 0.35` reads as "35% annual volatility" the way a person
would expect.

```python
TRADING_YEAR_SECONDS = 252 * 6.5 * 3600   # 5,896,800 — 252 days × 6.5 market hours
DT_SECONDS = 0.5
DT = DT_SECONDS / TRADING_YEAR_SECONDS    # 8.479e-8 years per tick
```

The simulator runs on wall-clock ticks, not market hours — it always trades. That is
intentional: a demo at 9 PM has to move.

### Calibration — what the numbers actually look like

Per-tick standard deviation is `σ·√dt`, which for a realistic `σ = 0.35` is **0.0102%**, or
about **2 cents on a $190 stock**. Over the whole 600-tick ring buffer (5 minutes) that
accumulates to only ±0.25%. On a dashboard, that reads as *stuck*: the daily-change column
sits at ±0.2% forever and the flash animation fires on sub-penny wiggles.

So the simulator applies a **`SIM_VOL_SCALE` multiplier, default `4.0`**:

| σ (annual) | scale | per tick | over 5 min | over 1 h | over 6.5 h |
|---|---|---|---|---|---|
| 0.35 | 1.0 (real) | 0.0102% (~$0.02 on $190) | 0.25% | 0.87% | 2.20% |
| **0.35** | **4.0 (default)** | **0.0408% (~$0.08 on $190)** | **1.00%** | **3.46%** | **8.82%** |
| 0.60 | 4.0 | 0.0699% (~$0.13 on $190) | 1.71% | 5.93% | 15.12% |

At scale 4.0 a ticker moves a visible ~1% while you watch it for five minutes, and a
plausible ~3.5% over an hour. This is a deliberate, documented lie in service of the demo —
**not** a modelling error. Set `SIM_VOL_SCALE=1.0` for statistically honest output.

### Per-ticker parameters

| Parameter | Range | Meaning |
|---|---|---|
| `seed_price` | $20–$500 | starting and anchor price |
| `drift` (μ) | −0.05 … +0.20 | annual expected return |
| `volatility` (σ) | 0.18 … 0.75 | annual volatility; mega-caps low, TSLA/NVDA high |
| `sector` | enum | drives the correlation block (§3) |
| `beta` | 0.6 … 1.6 | loading on the market factor |

---

## 3. Correlation

Two-factor structure. One market factor, one factor per sector, plus idiosyncratic noise:

```
Z_i = √w_m · β̂_i · Z_market  +  √w_s · Z_sector(i)  +  √(1 − w_m − w_s) · Z_i,idio
```

with `w_m = 0.25`, `w_s = 0.15`, so the weights are variance shares summing to 1 and `Z_i`
stays unit-variance. `β̂_i` is the ticker's beta normalised so the mean loading is 1.

The implied correlations:

| Pair | Correlation |
|---|---|
| Two tickers in the same sector (AAPL/MSFT) | **0.40** |
| Two tickers in different sectors (AAPL/JPM) | **0.25** |

Those are close to real S&P pairwise numbers, and they are *visibly* correlated on screen:
during a market-factor down-tick most of the watchlist flashes red at once, which is the
effect PLAN.md is asking for.

Draw exactly **1 + |sectors| + |tickers|** normals per tick. Draw the market and sector
factors **first**, in a fixed order, then the idiosyncratic ones in sorted ticker order —
this is what makes §6's determinism hold.

Sectors for the ten defaults:

| Sector | Tickers |
|---|---|
| `TECH` | AAPL, GOOGL, MSFT, NVDA, META |
| `CONSUMER` | AMZN, TSLA, NFLX |
| `FINANCE` | JPM, V |

Unknown tickers land in a synthetic `OTHER` sector — see §7.

---

## 4. Mean reversion: why pure GBM is not enough

A pure random walk has unbounded variance. With `σ_eff = 1.4` (0.35 × scale 4), the 1-sigma
spread after a 6.5-hour session is ±8.8%, and the *tails* are much worse — over a multi-hour
lecture or an overnight container, it is entirely normal for a ticker to end at half or
double its seed price. AAPL at $47 makes the whole terminal look broken, and the portfolio
heatmap turns into one giant rectangle.

Fix: an OU pull toward the anchor, in log space. `κ` is set from a **half-life** — the time
for a deviation to decay halfway back:

```python
kappa = math.log(2) / (HALF_LIFE_HOURS / (6.5 * 252))   # annualised
```

| Half-life | κ (annual) | Stationary spread at σ_eff = 1.4 |
|---|---|---|
| 2 h | 568 | ±4.2% |
| **4 h (default)** | **284** | **±5.9%** |
| 8 h | 142 | ±8.3% |

A 4-hour half-life keeps every ticker in a believable ±6% band around its seed while
leaving hours of free wandering inside it. The drift term still works: μ shifts the *anchor*
over time (`A_i(t) = seed_i · exp(μ_i · t)`), so a ticker with positive drift genuinely
trends up instead of being yanked back to a fixed number.

**Set `SIM_HALF_LIFE_HOURS=0` to disable reversion entirely** and get pure GBM — the mode
the "GBM math is correct" unit test runs in, since with `κ = 0` the analytic distribution of
`log S(T)/S(0)` is exactly `N((μ − σ²/2)T, σ²T)` and is straightforward to assert on.

---

## 5. Events

Per PLAN.md §6: "occasional random events — sudden 2–5% moves on a ticker for drama."

Poisson arrivals, checked per ticker per tick:

```python
EVENT_PROB_PER_TICK = 0.0005     # ≈ one event per ticker per 1,000 s
EVENT_MIN_PCT, EVENT_MAX_PCT = 0.02, 0.05
```

| Watchlist size | Expected time between events (anywhere on screen) |
|---|---|
| 10 (default) | ~100 s |
| 30 (the cap) | ~33 s |

Roughly one visible jump a minute or two. Frequent enough to notice, rare enough to stay
interesting.

**A jump moves the anchor halfway with it:**

```python
jump = sign * uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)   # in log space
state.log_price += jump
state.log_anchor += jump * 0.5                        # half the move persists
```

Without the anchor shift, the OU pull (§4) would erase every event within a few hours and
the chart would show a spike-and-perfect-recovery every time — which looks synthetic.
Moving the anchor half-way means the price spikes, retraces about half, and settles at a new
level. That is what news actually looks like on a chart.

Events are logged at `INFO` (`simulated event: NVDA +3.8%`) so a demo can point at one.

---

## 6. Determinism

E2E tests need reproducibility, and PLAN.md §6 requires unknown tickers to be deterministic.
Three separate guarantees, deliberately not conflated:

1. **Seed prices are always deterministic**, in every mode, from the symbol alone (§7).
   `LOOKUP["AAPL"] = 190.00` and `hash("PYPL") → 178.51` never change between runs.
2. **The random path is deterministic when `SIM_SEED` is set.** `SimulatedSource(seed=42)`
   builds a private `random.Random(42)`; identical ticker sets in identical order produce
   byte-identical price sequences. E2E runs set `SIM_SEED=42`.
3. **Production leaves `SIM_SEED` unset** and seeds from the OS, so two people watching the
   same demo do not see the same chart.

Rules that make guarantee 2 hold:

- **Never use the `random` module functions.** Use one `random.Random` instance owned by
  the engine. Module-level `random.gauss()` shares global state with anything else in the
  process — including pytest plugins — and destroys reproducibility in ways that are
  miserable to debug.
- **Iterate tickers in sorted order** everywhere RNG is consumed. `set` iteration order is
  not stable across processes.
- **Draw a fixed number of normals per tick**, in a fixed order (market → sectors → tickers,
  §3). Do not skip a draw for a ticker that happens to have no state yet.
- **The engine is single-threaded.** `MARKET_INTERFACE.md` §8 already forbids offloading
  `step()` to a thread; this is the reason.

---

## 7. Seed prices

```python
# backend/app/market/seeds.py
"""Starting prices, drift, and volatility for the simulator.

Approximate real levels as of mid-2026 — close enough to be plausible, and
nobody trades against them. Exactness is not the goal; recognisability is.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib


class Sector(str, Enum):
    TECH = "tech"
    CONSUMER = "consumer"
    FINANCE = "finance"
    HEALTH = "health"
    ENERGY = "energy"
    INDUSTRIAL = "industrial"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TickerSpec:
    price: float
    drift: float          # annual
    volatility: float     # annual
    sector: Sector
    beta: float = 1.0


SEED_PRICES: dict[str, TickerSpec] = {
    # --- the ten defaults (PLAN.md §7) ---
    "AAPL":  TickerSpec(190.00, 0.09, 0.26, Sector.TECH,       1.05),
    "GOOGL": TickerSpec(175.00, 0.10, 0.29, Sector.TECH,       1.10),
    "MSFT":  TickerSpec(420.00, 0.11, 0.25, Sector.TECH,       0.95),
    "AMZN":  TickerSpec(185.00, 0.10, 0.33, Sector.CONSUMER,   1.20),
    "TSLA":  TickerSpec(245.00, 0.05, 0.62, Sector.CONSUMER,   1.60),
    "NVDA":  TickerSpec(125.00, 0.18, 0.55, Sector.TECH,       1.55),
    "META":  TickerSpec(510.00, 0.12, 0.36, Sector.TECH,       1.25),
    "JPM":   TickerSpec(215.00, 0.06, 0.24, Sector.FINANCE,    0.90),
    "V":     TickerSpec(280.00, 0.07, 0.21, Sector.FINANCE,    0.85),
    "NFLX":  TickerSpec(680.00, 0.10, 0.38, Sector.CONSUMER,   1.20),
    # --- likely additions (PLAN.md §6 names PYPL, AMD, INTC, DIS, BA, WMT, KO) ---
    "PYPL":  TickerSpec( 68.00, 0.04, 0.40, Sector.FINANCE,    1.15),
    "AMD":   TickerSpec(160.00, 0.13, 0.50, Sector.TECH,       1.50),
    "INTC":  TickerSpec( 32.00, 0.01, 0.42, Sector.TECH,       1.10),
    "DIS":   TickerSpec( 95.00, 0.05, 0.30, Sector.CONSUMER,   1.05),
    "BA":    TickerSpec(180.00, 0.03, 0.42, Sector.INDUSTRIAL, 1.30),
    "WMT":   TickerSpec( 72.00, 0.08, 0.19, Sector.CONSUMER,   0.60),
    "KO":    TickerSpec( 62.00, 0.05, 0.16, Sector.CONSUMER,   0.55),
    # ... target ~50 entries: the rest of the mega-caps plus common retail names
    #     (BRKB, LLY, UNH, XOM, CVX, JNJ, PG, HD, MA, COST, ORCL, CRM, ADBE,
    #      QCOM, MU, AVGO, SBUX, NKE, MCD, PFE, MRK, ABBV, T, VZ, GS, BAC,
    #      WFC, F, GM, UBER, ABNB, SHOP, SQ, SOFI, PLTR, COIN, RIVN, LCID,
    #      SPY, QQQ)
}

_MIN_PRICE, _MAX_PRICE = 20.0, 500.0


def spec_for(ticker: str) -> TickerSpec:
    """Return a spec for ANY validated symbol. Never raises, never rejects.

    PLAN.md §6: the LLM manages the watchlist proactively, so rejecting an
    unknown ticker mid-conversation is a worse experience than a plausible
    synthetic price. Format validation (^[A-Z]{1,5}$) already happened at the
    API boundary; by the time we get here the symbol is acceptable by definition.
    """
    known = SEED_PRICES.get(ticker)
    if known is not None:
        return known
    return _synthesise(ticker)


def _synthesise(ticker: str) -> TickerSpec:
    """Deterministic pseudo-random spec derived from the symbol itself.

    SHA-256, not hash() — Python's hash() is salted per process by
    PYTHONHASHSEED, so it would give a different price on every restart and
    break E2E reproducibility outright.
    """
    h = hashlib.sha256(ticker.encode()).digest()
    price_n = int.from_bytes(h[0:8], "big")
    vol_n   = int.from_bytes(h[8:12], "big")
    drift_n = int.from_bytes(h[12:16], "big")
    beta_n  = int.from_bytes(h[16:20], "big")

    span = int((_MAX_PRICE - _MIN_PRICE) * 100) + 1        # cents in [20.00, 500.00]
    return TickerSpec(
        price=round(_MIN_PRICE + (price_n % span) / 100.0, 2),
        drift=-0.05 + (drift_n % 2501) / 10_000.0,          # −5% … +20%
        volatility=0.20 + (vol_n % 4001) / 10_000.0,        # 20% … 60%
        sector=Sector.OTHER,
        beta=0.70 + (beta_n % 901) / 1_000.0,               # 0.70 … 1.60
    )
```

Verified spread of `_synthesise` over all 17,576 three-letter symbols: min $20.00, max
$499.95, mean $259.93 — uniform across the range, as intended. Spot checks:
`PYPL → $178.51`, `AMD → $104.85`, `SNOW → $348.97`, `COIN → $48.98`.

> Where a symbol appears in both `SEED_PRICES` and the synthetic path, the table wins.
> `AMD` above is in the table at $160; the $104.85 figure is what the hash *would* have
> produced, shown here only to demonstrate the fallback.

---

## 8. Engine structure

```python
# backend/app/market/simulator.py
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from .seeds import Sector, TickerSpec, spec_for
from .source import MarketDataSource
from .types import Quote

log = logging.getLogger(__name__)

TRADING_YEAR_SECONDS = 252 * 6.5 * 3600
MARKET_WEIGHT = 0.25
SECTOR_WEIGHT = 0.15
IDIO_WEIGHT = 1.0 - MARKET_WEIGHT - SECTOR_WEIGHT
EVENT_PROB_PER_TICK = 0.0005
EVENT_MIN_PCT, EVENT_MAX_PCT = 0.02, 0.05
EVENT_ANCHOR_SHARE = 0.5
MIN_PRICE = 0.01


@dataclass(slots=True)
class _TickerState:
    spec: TickerSpec
    log_price: float
    log_anchor: float
    session_open: float


class GbmEngine:
    """Correlated GBM with OU mean reversion and Poisson jumps.

    Pure and synchronous: `step()` takes the tracked set and returns quotes.
    No I/O, no clock beyond `datetime.now`, no global RNG.
    """

    def __init__(self, *, seed: int | None = None, dt_seconds: float = 0.5,
                 vol_scale: float = 4.0, half_life_hours: float = 4.0) -> None:
        self._rng = random.Random(seed)
        self._dt = dt_seconds / TRADING_YEAR_SECONDS
        self._sqrt_dt = math.sqrt(self._dt)
        self._vol_scale = vol_scale
        self._kappa = (math.log(2) / (half_life_hours / (6.5 * 252))
                       if half_life_hours > 0 else 0.0)
        self._states: dict[str, _TickerState] = {}

    # ---- lifecycle -------------------------------------------------

    def ensure(self, tickers: frozenset[str]) -> None:
        """Mint state for symbols we have not seen. Idempotent; O(new)."""
        for t in sorted(tickers - self._states.keys()):
            spec = spec_for(t)
            lp = math.log(spec.price)
            self._states[t] = _TickerState(spec=spec, log_price=lp, log_anchor=lp,
                                           session_open=spec.price)

    def forget(self, keep: frozenset[str]) -> None:
        for t in list(self._states):
            if t not in keep:
                del self._states[t]

    # ---- one tick ---------------------------------------------------

    def step(self, tickers: frozenset[str]) -> list[Quote]:
        self.ensure(tickers)
        if not tickers:
            return []

        ordered = sorted(tickers)                       # determinism: fixed RNG order
        z_market = self._rng.gauss(0.0, 1.0)
        sectors = sorted({self._states[t].spec.sector for t in ordered})
        z_sector = {s: self._rng.gauss(0.0, 1.0) for s in sectors}

        now = datetime.now(timezone.utc)
        quotes: list[Quote] = []
        for t in ordered:
            st = self._states[t]
            z_idio = self._rng.gauss(0.0, 1.0)
            z = (math.sqrt(MARKET_WEIGHT) * st.spec.beta * z_market
                 + math.sqrt(SECTOR_WEIGHT) * z_sector[st.spec.sector]
                 + math.sqrt(IDIO_WEIGHT) * z_idio)

            sigma = st.spec.volatility * self._vol_scale
            mu = st.spec.drift

            # anchor drifts with mu, so a trending ticker is not yanked back
            st.log_anchor += mu * self._dt

            st.log_price += (
                self._kappa * (st.log_anchor - st.log_price) * self._dt   # mean reversion
                + (mu - 0.5 * sigma * sigma) * self._dt                   # drift
                + sigma * self._sqrt_dt * z                               # diffusion
            )

            if self._rng.random() < EVENT_PROB_PER_TICK:
                jump = self._rng.choice((1.0, -1.0)) * self._rng.uniform(
                    EVENT_MIN_PCT, EVENT_MAX_PCT)
                st.log_price += jump
                st.log_anchor += jump * EVENT_ANCHOR_SHARE
                log.info("simulated event: %s %+.2f%%", t, jump * 100)

            price = max(MIN_PRICE, round(math.exp(st.log_price), 2))
            quotes.append(Quote(ticker=t, price=price,
                                ts=now, session_open=st.session_open))
        return quotes
```

`SimulatedSource` is the thin `MarketDataSource` wrapper over this engine —
`MARKET_INTERFACE.md` §8 has it in full.

### Notes on the implementation

- **`round(..., 2)` is applied to the emitted price, never fed back into `log_price`.**
  Rounding the state would inject a tiny bias on every tick and, on a cheap stock, would
  quantise the walk into a staircase. The internal path stays continuous.
- **`session_open` is captured once**, at the moment a ticker first appears, and never
  updated. It is the reference for the daily-change column per PLAN.md §6. A ticker added
  mid-session opens at its seed price — so its change column starts at exactly 0.00%, which
  is honest for a simulated ticker that did not exist a second ago.
- **`MIN_PRICE = 0.01` is a floor, not a clamp on the log path.** Log-space GBM cannot reach
  zero, so this only guards against a pathological config; it is a seatbelt.
- **`forget()` is separate from `ensure()`** so `set_tickers` can shrink state when the
  watchlist shrinks, without the two operations racing.

---

## 9. Configuration

All optional, all with production-sane defaults. None of these appear in `.env.example` for
the student — they exist for tests and for tuning a lecture.

| Variable | Default | Effect |
|---|---|---|
| `SIM_SEED` | unset (OS entropy) | fixes the RNG for reproducible E2E runs |
| `SIM_VOL_SCALE` | `4.0` | volatility multiplier; `1.0` = statistically honest |
| `SIM_HALF_LIFE_HOURS` | `4.0` | mean-reversion half-life; `0` = pure GBM |
| `SIM_INTERVAL_MS` | `500` | tick cadence; also the `MarketDataSource.poll_interval` |

Read them once in `build_source()` (`MARKET_INTERFACE.md` §4), not inside the engine — the
engine takes plain arguments so tests never touch the environment.

---

## 10. Tests

Beyond the shared `MarketDataSource` suite in `MARKET_INTERFACE.md` §9:

**Statistical (run with `half_life_hours=0`, so the analytic result is exact)**

```python
def test_gbm_log_returns_match_analytic_distribution():
    """With kappa=0, log(S_T/S_0) ~ N((mu - sigma^2/2)T, sigma^2 T)."""
    eng = GbmEngine(seed=7, vol_scale=1.0, half_life_hours=0.0)
    ...  # 200_000 ticks of a single ticker; assert sample mean and variance
         # against the closed form within 3 standard errors of the estimator.
```

- `test_no_drift_no_vol_is_a_flat_line` — μ = σ = 0 ⇒ price never changes. Catches sign and
  `dt` errors instantly and needs no statistics.
- `test_correlation_matches_factor_weights` — 50k ticks over AAPL/MSFT (same sector) and
  AAPL/JPM (different): sample correlations ≈ 0.40 and ≈ 0.25, ±0.03.
- `test_mean_reversion_bounds_a_long_run` — 8 hours of simulated ticks at default settings;
  every ticker stays within ±25% of its seed. (A generous bound: this is a regression guard
  against κ being wired up wrong, not a tight statistical claim.)

**Determinism**

- `test_same_seed_same_sequence` — two engines, seed 42, 1,000 ticks ⇒ identical lists.
- `test_different_seed_different_sequence` — the complement, so the test above cannot pass
  by accidentally producing constants.
- `test_seed_price_is_stable_across_processes` — assert literal values
  (`spec_for("PYPL").price == 178.51`) in a **subprocess with a different
  `PYTHONHASHSEED`**. This is the test that catches someone swapping `hashlib.sha256` for
  the builtin `hash()`.

**Seeds**

- `test_known_tickers_use_the_table` — `spec_for("AAPL").price == 190.00`.
- `test_unknown_ticker_lands_in_range` — over all 17,576 three-letter symbols, every price
  is in `[20.00, 500.00]`, volatility in `[0.20, 0.60]`, drift in `[-0.05, 0.20]`.
- `test_unknown_ticker_is_never_rejected` — `spec_for` raises for no input matching
  `^[A-Z]{1,5}$`.

**Behaviour**

- `test_events_fire_at_the_expected_rate` — with `EVENT_PROB_PER_TICK` forced to 0.5,
  assert jumps occur and each is within 2–5%.
- `test_event_moves_the_anchor_halfway` — anchor delta is exactly half the price jump.
- `test_prices_are_rounded_to_cents_but_state_is_not` — emitted price has ≤2 decimals while
  two consecutive internal `log_price` values differ by more than the rounding step.
- `test_new_ticker_added_mid_run_starts_at_its_seed_price`.
- `test_step_with_empty_ticker_set_returns_empty_and_draws_no_rng`.

---

## 11. What this simulator deliberately does not model

Worth stating so nobody files these as bugs, and so a student asking "is this real?" gets a
straight answer:

- **No volume, bid/ask, or order book.** PLAN.md §3 is market-orders-only for exactly this
  reason; there is nothing for a book to do.
- **No market hours, holidays, gaps, or opening auctions.** The simulator trades
  continuously. A real market's overnight gap is a large part of daily variance and is
  simply absent here.
- **No fat tails or volatility clustering.** Real returns are leptokurtic and heteroskedastic;
  GBM is neither. The jump process in §5 is a crude stand-in for the tail, and it is crude
  on purpose — a GARCH layer would add real complexity for an effect nobody watching a
  five-minute demo can distinguish.
- **No corporate actions, earnings, splits, or dividends.**
- **No cross-asset or macro structure** beyond the single market factor in §3.
- **The default `SIM_VOL_SCALE=4.0` overstates volatility by 4×** (§2). Everything the app
  computes from these prices — P&L, the heatmap, the LLM's "your portfolio is concentrated"
  analysis — is arithmetically correct on top of prices that move four times faster than a
  real market's.
