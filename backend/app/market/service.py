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

        A 401 (or DNS failure, or a corporate proxy eating the request) out of
        MassiveSource.start() means "log bad key and build a SimulatedSource
        instead" — caught HERE, one place, covering every start-time failure, so a
        typo in .env can never take the app down.
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
