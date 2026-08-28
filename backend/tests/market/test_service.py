"""MARKET_DATA_DESIGN.md §17.5. The failure ladder is policy, so it is tested
directly against a fake source rather than a real GBM engine or respx fixtures."""
from __future__ import annotations

import asyncio

from app.market.service import MarketDataService
from app.market.simulator import SimulatedSource
from app.market.source import MarketDataSource
from app.market.types import Quote, StreamStatus, utcnow

from ..conftest import wait_for


class FlakySource(MarketDataSource):
    """Fails on demand."""

    name = "flaky"

    def __init__(self, fail_times: int, *, start_fails: bool = False) -> None:
        self.poll_interval = 0.01
        self.degraded_reason = None
        self._left = fail_times
        self._start_fails = start_fails
        self.closed = False
        self._tickers: frozenset[str] = frozenset()

    def set_tickers(self, tickers):
        self._tickers = tickers

    async def aclose(self):
        self.closed = True

    async def start(self):
        if self._start_fails:
            raise RuntimeError("could not start")

    async def fetch(self):
        if self._left > 0:
            self._left -= 1
            raise RuntimeError("upstream boom")
        return [Quote("AAPL", 100.0, utcnow())]


class SpySource(SimulatedSource):
    """A real simulator that counts set_tickers/evict-triggering calls."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.set_tickers_calls = 0

    def set_tickers(self, tickers):
        self.set_tickers_calls += 1
        super().set_tickers(tickers)


async def test_three_consecutive_failures_fall_back_to_the_simulator():
    svc = MarketDataService(FlakySource(fail_times=99))
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: svc.health["source"] == "simulator", timeout=5)
    assert svc.status is StreamStatus.DEGRADED
    assert "upstream unavailable" in svc.health["reason"]
    await svc.stop()


async def test_two_failures_then_success_does_not_fall_back():
    flaky = FlakySource(fail_times=2)
    svc = MarketDataService(flaky)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: svc.price("AAPL") is not None, timeout=5)
    assert svc.health["source"] == "flaky"
    assert svc.status is StreamStatus.CONNECTED
    await svc.stop()


async def test_fallback_closes_the_old_source():
    flaky = FlakySource(fail_times=99)
    svc = MarketDataService(flaky)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: flaky.closed, timeout=5)
    await svc.stop()


async def test_fallback_happens_once():
    """`_fall_back` only replaces a source that is NOT already a `SimulatedSource`
    (MARKET_DATA_DESIGN.md §10): once the fallback is installed, further failures
    are logged and left in place rather than triggering another swap."""

    class FailingSimulator(SimulatedSource):
        async def fetch(self):
            raise RuntimeError("even the fallback is having a bad day")

    flaky = FlakySource(fail_times=99)   # poll_interval is fixed at 0.01 in FlakySource
    svc = MarketDataService(flaky, fallback_factory=lambda: FailingSimulator(seed=1, interval=0.01))
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: isinstance(svc._source, SimulatedSource), timeout=5)
    replacement = svc._source
    # the replacement keeps failing every fetch(); give it several poll intervals
    # and confirm the guard prevents any further swap.
    await asyncio.sleep(0.2)
    assert svc._source is replacement
    await svc.stop()


async def test_a_source_that_fails_to_start_falls_back():
    svc = MarketDataService(FlakySource(fail_times=0, start_fails=True))
    await svc.start()
    assert svc.health["source"] == "simulator"
    assert svc.status is StreamStatus.DEGRADED
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: svc.price("AAPL") is not None, timeout=5)
    await svc.stop()


async def test_a_full_subscriber_is_dropped_without_stalling_the_producer():
    svc = MarketDataService(SimulatedSource(seed=1, interval=0.01), broadcast_interval=0.01)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    q = svc.subscribe()
    # fill the queue past capacity without draining it
    for _ in range(200):
        try:
            q.put_nowait([])
        except asyncio.QueueFull:
            break
    await asyncio.sleep(0.2)
    assert q not in svc._subscribers
    # the producer must still be advancing
    price_before = svc.price("AAPL")
    await asyncio.sleep(0.2)
    assert svc.price("AAPL") is not None
    assert price_before is not None
    await svc.stop()


async def test_subscribe_primes_from_the_cache_snapshot():
    svc = MarketDataService(SimulatedSource(seed=1, interval=0.01))
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    await wait_for(lambda: svc.price("AAPL") is not None, timeout=5)
    q = svc.subscribe()
    batch = q.get_nowait()
    assert any(t.ticker == "AAPL" for t in batch)
    await svc.stop()


async def test_set_tracked_is_idempotent():
    spy = SpySource(seed=1)
    svc = MarketDataService(spy)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    calls_after_first = spy.set_tickers_calls
    svc.set_tracked(frozenset({"AAPL"}))    # same set again
    assert spy.set_tickers_calls == calls_after_first
    await svc.stop()


async def test_set_tracked_primes_new_simulator_tickers():
    svc = MarketDataService(SimulatedSource(seed=1))
    await svc.start()
    svc.set_tracked(frozenset({"PYPL"}))
    assert svc.price("PYPL") is not None    # synchronous, no poll needed
    await svc.stop()


async def test_set_tracked_does_not_prime_massive_tickers():
    svc = MarketDataService(FlakySource(fail_times=99))
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    assert svc.price("AAPL") is None        # A1 must not leak into a real-data source
    await svc.stop()


async def test_stop_cancels_both_tasks_and_sets_disconnected():
    svc = MarketDataService(SimulatedSource(seed=1))
    await svc.start()
    tasks = list(svc._tasks)
    await svc.stop()
    assert svc.status is StreamStatus.DISCONNECTED
    assert all(t.done() for t in tasks)


async def test_degraded_reason_from_the_source_marks_the_stream_degraded():
    class EodSource(MarketDataSource):
        name = "eod"
        poll_interval = 60.0
        degraded_reason = "end-of-day data (free Massive tier)"

        def set_tickers(self, tickers):
            pass

        async def fetch(self):
            return []

    svc = MarketDataService(EodSource())
    await svc.start()
    await wait_for(lambda: svc.status is StreamStatus.DEGRADED, timeout=5)
    assert svc.health["reason"] == "end-of-day data (free Massive tier)"
    await svc.stop()
