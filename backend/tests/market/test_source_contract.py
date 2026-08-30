"""The interface earns its keep only if both implementations are held to the same
suite (MARKET_DATA.md §10)."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.market.massive import MassiveSource
from app.market.simulator import SimulatedSource

from ..conftest import load_fixture


def _snapshot_callback(request):
    """Mimic the real API's server-side filtering: only return rows for the
    tickers actually requested. `MassiveClient.snapshot()` does not filter the
    response itself -- it trusts the upstream to -- so a mock that always returns
    every ticker regardless of the query would silently break any test asserting
    that an untracked ticker is never quoted."""
    requested = set((request.url.params.get("tickers") or "").split(","))
    fixture = load_fixture("massive", "snapshot_aapl_msft.json")
    rows = [row for row in fixture["tickers"] if row["ticker"] in requested]
    return Response(200, json={**fixture, "tickers": rows, "count": len(rows)})


@pytest.fixture(params=["simulator", "massive"])
async def source(request):
    if request.param == "simulator":
        src = SimulatedSource(seed=42)
        await src.start()
        yield src
        await src.aclose()
        return
    with respx.mock(base_url="https://api.massive.com", assert_all_called=False) as mock:
        mock.get(path__startswith="/v2/snapshot").mock(side_effect=_snapshot_callback)
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
    assert (await source.fetch()) is not None


async def test_aclose_is_idempotent(source):
    await source.aclose()
    await source.aclose()


async def test_untracked_ticker_is_never_quoted(source):
    source.set_tickers(frozenset({"AAPL"}))
    assert all(q.ticker == "AAPL" for q in await source.fetch())
