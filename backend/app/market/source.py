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
        has no price until the next poll (MARKET_DATA_DESIGN.md §7.4).

        SimulatedSource overrides it to return the seed price immediately, which
        is not an invention — the seed price is the ticker's opening price by
        definition.
        """
        return []

    @abc.abstractmethod
    def set_tickers(self, tickers: frozenset[str]) -> None:
        """Replace the tracked set. Called on every watchlist/position change."""

    @abc.abstractmethod
    async def fetch(self) -> list[Quote]:
        """One poll round."""
