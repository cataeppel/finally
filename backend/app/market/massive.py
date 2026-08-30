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
        # the 403 body shape is reported but unverified (MARKET_DATA.md §5).
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
        # Filter to what we asked for. Upstream should only return the requested
        # symbols, but a source must never quote a ticker outside the tracked set --
        # the cache would mint state for it and the SSE stream would carry it.
        quotes = [
            q
            for row in body.get("tickers", ())
            if row.get("ticker") in tickers and (q := _parse_snapshot_row(row))
        ]
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
        """Optional chart backfill. A minute with no trades produces no bar, so the
        series has gaps — never assume evenly spaced points."""
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
