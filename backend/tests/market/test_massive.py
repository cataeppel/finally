"""MARKET_DATA.md §10. All via respx -- never hit api.massive.com."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
import respx

from app.market.massive import (
    GROUPED,
    SNAPSHOT,
    MassiveAuthError,
    MassiveClient,
    MassiveRateLimitError,
    MassiveSource,
    _ms_to_dt,
    _ns_to_dt,
    _parse_snapshot_row,
)

from ..conftest import load_fixture

BASE = "https://api.massive.com"


# ---- MassiveSource: mode selection -----------------------------------------


async def test_403_on_the_probe_selects_grouped_mode():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get(path__startswith="/v2/snapshot").mock(return_value=httpx.Response(403))
        mock.get(path__startswith="/v2/aggs/grouped").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "grouped_daily.json"))
        )
        src = MassiveSource(api_key="k")
        await src.start()
        assert src._mode == GROUPED
        assert src.poll_interval == 60.0
        assert src.degraded_reason is not None
        await src.aclose()


async def test_401_propagates_out_of_start():
    with respx.mock(base_url=BASE) as mock:
        mock.get(path__startswith="/v2/snapshot").mock(return_value=httpx.Response(401))
        src = MassiveSource(api_key="bad")
        with pytest.raises(MassiveAuthError):
            await src.start()
        await src.aclose()


async def test_403_mid_flight_switches_mode_without_raising():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        snapshot_route = mock.get(path__startswith="/v2/snapshot").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        mock.get(path__startswith="/v2/aggs/grouped").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "grouped_daily.json"))
        )
        src = MassiveSource(api_key="k")
        await src.start()
        assert src._mode == SNAPSHOT

        snapshot_route.mock(return_value=httpx.Response(403))    # reconfigure the SAME route
        src.set_tickers(frozenset({"AAPL"}))
        quotes = await src.fetch()          # must not raise
        assert quotes == []
        assert src._mode == GROUPED
        await src.aclose()


async def test_429_raises_the_interval_and_re_raises():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        snapshot_route = mock.get(path__startswith="/v2/snapshot").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        src = MassiveSource(api_key="k")
        await src.start()
        start_interval = src.poll_interval

        snapshot_route.mock(return_value=httpx.Response(429))
        src.set_tickers(frozenset({"AAPL"}))
        with pytest.raises(MassiveRateLimitError):
            await src.fetch()
        assert src.poll_interval > start_interval
        await src.aclose()


async def test_429_interval_is_monotonic():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        snapshot_route = mock.get(path__startswith="/v2/snapshot").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        src = MassiveSource(api_key="k")
        await src.start()
        src.set_tickers(frozenset({"AAPL"}))

        snapshot_route.mock(return_value=httpx.Response(429))
        with pytest.raises(MassiveRateLimitError):
            await src.fetch()
        raised_interval = src.poll_interval

        snapshot_route.mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        await src.fetch()
        assert src.poll_interval == raised_interval   # a success does not lower it
        await src.aclose()


async def test_trading_date_walks_back_over_a_weekend():
    empty = load_fixture("massive", "empty_results.json")
    populated = load_fixture("massive", "grouped_daily.json")
    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path__startswith="/v2/aggs/grouped")
        route.side_effect = [
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=empty),
            httpx.Response(200, json=populated),
        ]
        client = MassiveClient(api_key="k", base_url=BASE)
        day = await client.latest_trading_date(max_lookback=5)
        assert route.call_count == 4
        assert day is not None
        await client.aclose()


async def test_trading_date_refreshes_once_per_et_day():
    """`_refresh_date_if_stale` compares `_date_resolved_on` against the real
    current ET date, so the test drives it by setting that field directly rather
    than mocking `datetime.now` -- simpler and does not risk breaking every other
    `datetime` call in the module."""
    from app.market.massive import EASTERN

    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get(path__startswith="/v2/snapshot").mock(return_value=httpx.Response(403))
        route = mock.get(path__startswith="/v2/aggs/grouped").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "grouped_daily.json"))
        )
        src = MassiveSource(api_key="k")
        await src.start()          # 1 grouped call (latest_trading_date via _switch_to_grouped)
        src.set_tickers(frozenset({"AAPL"}))

        today = datetime.now(EASTERN).date()
        src._date_resolved_on = today
        before = route.call_count                           # 1: resolution inside start()
        await src.fetch()          # same ET day -> the data call only, no re-resolution
        assert route.call_count == before + 1

        src._date_resolved_on = today - timedelta(days=1)   # simulate a stale rollover
        await src.fetch()          # re-resolve the date, THEN fetch the bars
        assert route.call_count == before + 3
        await src.aclose()


async def test_bearer_header_is_used_and_the_key_is_never_in_a_url():
    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path__startswith="/v2/snapshot").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        client = MassiveClient(api_key="super-secret-key", base_url=BASE)
        await client.snapshot({"AAPL"})
        request = route.calls.last.request
        assert request.headers.get("authorization") == "Bearer super-secret-key"
        assert "super-secret-key" not in str(request.url)
        await client.aclose()


# ---- parsing ----------------------------------------------------------------


def test_nanosecond_and_millisecond_timestamps_both_parse():
    ns = _ns_to_dt(1605192894630916600)
    ms = _ms_to_dt(1602705600000)
    assert 2020 <= ns.year <= 2021
    assert 2020 <= ms.year <= 2021


def test_zero_filled_day_falls_through_to_prev_close():
    """In the 3:30-4:00 AM ET cleared-snapshot window `day` is zero-filled. Both the
    price fallback (lastTrade -> min -> day -> prevDay.c) and the session_open
    fallback (day.o -> prevDay.c -> price) bottom out at prevDay's CLOSE -- there is
    no prevDay.o in the chain (MARKET_DATA.md §5), so with day and min also
    empty, price and session_open both land on prevDay.c."""
    row = {
        "ticker": "AAPL",
        "day": {"o": 0, "h": 0, "l": 0, "c": 0},
        "min": {},
        "prevDay": {"o": 188.0, "c": 189.0},
        "lastTrade": {},
        "updated": 0,
    }
    q = _parse_snapshot_row(row)
    assert q is not None
    assert q.price == 189.0
    assert q.session_open == 189.0


def test_snapshot_row_without_any_price_returns_none():
    row = {"ticker": "ZZZZ", "day": {}, "min": {}, "prevDay": {}, "lastTrade": {}}
    assert _parse_snapshot_row(row) is None


async def test_missing_tickers_are_absent_not_errors():
    with respx.mock(base_url=BASE) as mock:
        mock.get(path__startswith="/v2/snapshot").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "snapshot_aapl_msft.json"))
        )
        client = MassiveClient(api_key="k", base_url=BASE)
        quotes = await client.snapshot({"AAPL", "ZZZZ"})
        assert {q.ticker for q in quotes} == {"AAPL"}
        await client.aclose()


async def test_grouped_keeps_only_tracked_tickers():
    with respx.mock(base_url=BASE) as mock:
        mock.get(path__startswith="/v2/aggs/grouped").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "grouped_daily.json"))
        )
        client = MassiveClient(api_key="k", base_url=BASE)
        quotes = await client.grouped_daily("2026-08-26", {"AAPL", "MSFT"})
        assert {q.ticker for q in quotes} == {"AAPL", "MSFT"}
        await client.aclose()


async def test_grouped_quote_timestamp_is_the_bar_not_now():
    """The C3 regression guard: two polls of the same body produce equal Quotes,
    so PriceCache.apply() dedupes them instead of emitting a fresh `flat` tick."""
    with respx.mock(base_url=BASE) as mock:
        mock.get(path__startswith="/v2/aggs/grouped").mock(
            return_value=httpx.Response(200, json=load_fixture("massive", "grouped_daily.json"))
        )
        client = MassiveClient(api_key="k", base_url=BASE)
        first = await client.grouped_daily("2026-08-26", {"AAPL"})
        second = await client.grouped_daily("2026-08-26", {"AAPL"})
        assert first == second
        await client.aclose()
