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
#: else added TSLA. See MARKET_DATA.md §4.
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
        self._event_prob = event_prob          # 0.0 isolates the diffusion
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
            # `sigma` below means precisely what the seed table says.
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
