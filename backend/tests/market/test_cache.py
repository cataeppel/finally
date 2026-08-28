from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.market.cache import RING_SIZE, PriceCache
from app.market.types import Quote


def _ts(seconds: float = 0.0) -> datetime:
    return datetime(2026, 8, 26, 14, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_first_quote_is_flat_and_pins_the_session_open():
    cache = PriceCache()
    tick = cache.apply(Quote("AAPL", 190.0, _ts(), session_open=None))
    assert tick.direction == "flat"
    assert tick.prev_price == tick.price == 190.0
    assert tick.open == 190.0


def test_change_pct_is_measured_against_open_and_direction_against_prev():
    cache = PriceCache()
    cache.apply(Quote("AAPL", 100.0, _ts(0), session_open=100.0))
    cache.apply(Quote("AAPL", 110.0, _ts(1), session_open=100.0))
    tick = cache.apply(Quote("AAPL", 109.0, _ts(2), session_open=100.0))
    assert tick.direction == "down"        # vs prev_price (110)
    assert tick.change_pct == 9.0          # vs session_open (100)


def test_a_repeated_identical_quote_returns_none():
    cache = PriceCache()
    q = Quote("AAPL", 190.0, _ts(0))
    cache.apply(q)
    assert cache.apply(Quote("AAPL", 190.0, _ts(0))) is None
    assert cache.apply(Quote("AAPL", 190.0, _ts(-1))) is None  # older ts, same price
    assert len(cache.history("AAPL")) == 1                     # ring did not grow


def test_session_open_is_not_overwritten_by_a_none():
    cache = PriceCache()
    cache.apply(Quote("AAPL", 190.0, _ts(0), session_open=188.0))
    tick = cache.apply(Quote("AAPL", 191.0, _ts(1), session_open=None))
    assert tick.open == 188.0


def test_ring_buffer_caps_at_600_and_drops_oldest_first():
    cache = PriceCache()
    for i in range(700):
        cache.apply(Quote("AAPL", 100.0 + i, _ts(i)))
    hist = cache.history("AAPL")
    assert len(hist) == RING_SIZE == 600
    assert hist[0].price == 100.0 + 100     # the 101st quote (index 100) survives


def test_evict_drops_untracked_state_and_its_ring():
    cache = PriceCache()
    cache.apply(Quote("AAPL", 190.0, _ts()))
    cache.apply(Quote("MSFT", 420.0, _ts()))
    cache.evict(frozenset({"AAPL"}))
    assert cache.get("MSFT") is None
    assert cache.history("MSFT") == []
    assert cache.get("AAPL") is not None


def test_history_of_an_unknown_ticker_is_empty():
    cache = PriceCache()
    assert cache.history("ZZZZ") == []


def test_zero_open_does_not_divide_by_zero():
    cache = PriceCache()
    tick = cache.apply(Quote("PENNY", 0.0, _ts(), session_open=0.0))
    assert tick.change_pct == 0.0


def test_snapshot_returns_every_tracked_tickers_current_tick():
    cache = PriceCache()
    cache.apply(Quote("AAPL", 190.0, _ts()))
    cache.apply(Quote("MSFT", 420.0, _ts()))
    snap = cache.snapshot()
    assert set(snap) == {"AAPL", "MSFT"}
    assert snap["AAPL"].price == 190.0


def test_price_returns_none_for_an_unknown_ticker():
    cache = PriceCache()
    assert cache.price("ZZZZ") is None
