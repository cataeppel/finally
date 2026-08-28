# MASSIVE_API.md — Market Data from Massive (formerly Polygon.io)

**Status:** research reference for the Market Data agent.
**Scope:** everything FinAlly needs to fetch live/EOD prices for a small set of US equity
tickers over REST. Written against the docs as of **2026-08-28**.

> **Read this first.** The single most important fact in this document is in §3:
> **the free tier cannot call the snapshot endpoints at all.** Any implementation that
> assumes `/v2/snapshot/...` works with a free key will 403 on every poll for most users
> who set `MASSIVE_API_KEY`. §5 gives the two-endpoint strategy that handles both cases.

---

## 1. Identity, base URL, and the rebrand

Polygon.io renamed itself to **Massive** on **2025-10-30**. The platform, API surface,
API keys, and account system are unchanged — only the brand and domain moved.

| | Value |
|---|---|
| Docs home | `https://massive.com/docs` |
| Machine-readable docs index | `https://massive.com/docs/llms.txt` (every endpoint page is also available as `.md`) |
| **Current REST base URL** | `https://api.massive.com` |
| Legacy REST base URL | `https://api.polygon.io` (still works, no deprecation date announced) |
| WebSocket base | `wss://socket.massive.com` |
| Dashboard / keys | `https://massive.com/dashboard/keys` |
| Official Python SDK | `pip install massive` (package `massive`, currently **2.8.0**) |
| Legacy Python SDK | `pip install polygon-api-client` (currently 1.16.3) |

**Path shapes are identical on both hosts.** `https://api.massive.com/v2/aggs/ticker/AAPL/prev`
and `https://api.polygon.io/v2/aggs/ticker/AAPL/prev` are the same endpoint. Use
`api.massive.com`; make the host a module constant so it is one edit if that ever changes.

### Why not the SDK

FinAlly's backend is an async FastAPI process with a background poller. The `massive`
SDK's `RESTClient` is **synchronous** and paginates with blocking generators — calling it
from the event loop would stall the SSE stream, and wrapping every call in
`asyncio.to_thread` buys nothing over just issuing the HTTP request. We need exactly two
endpoints. **Use `httpx.AsyncClient` directly** (see §7). The SDK is documented in §8 only
as a cross-check when debugging a response shape.

---

## 2. Authentication

Two methods, **both verified working** against `api.massive.com` on 2026-08-28:

**A. Query parameter** (what the SDK and most examples use)

```bash
curl "https://api.massive.com/v2/aggs/ticker/AAPL/prev?apiKey=$MASSIVE_API_KEY"
```

**B. Bearer header** (preferred — keeps the key out of URLs, logs, and error messages)

```bash
curl -H "Authorization: Bearer $MASSIVE_API_KEY" \
     "https://api.massive.com/v2/aggs/ticker/AAPL/prev"
```

> **FinAlly uses the Bearer header.** We log request URLs on failure (§6); a key in the
> query string ends up in container logs and in any exception text that reaches the user.
>
> One exception: paginated responses return a `next_url` that **does not carry the key**.
> If you follow one, re-attach auth — with the header this is automatic since you keep
> using the same client. FinAlly never paginates (see §5), so this is informational.

### Verified 401 responses

```bash
# No key at all
$ curl -s "https://api.massive.com/v2/aggs/ticker/AAPL/prev"
HTTP/2 401
{"status":"ERROR","request_id":"7779d0a2...","error":"API Key was not provided"}

# Bad key (either auth method)
$ curl -s -H "Authorization: Bearer BOGUSKEY123" "https://api.massive.com/v2/aggs/ticker/AAPL/prev"
HTTP/2 401
{"status":"ERROR","request_id":"e0290053...","error":"Unknown API Key"}
```

Every response — success or failure — carries an `x-request-id` header that also appears
as `request_id` in the body. **Log it on every failure**; it is what Massive support asks
for first.

---

## 3. Plans, entitlements, and rate limits — the part that decides the design

| Plan | Price | Rate limit | Recency | History |
|---|---|---|---|---|
| **Stocks Basic** (free) | $0 | **5 requests / minute** | **End-of-day** | 2 years |
| Stocks Starter | $29/mo | Unlimited | 15-minute delayed | 5 years |
| Stocks Developer | $79/mo | Unlimited | 15-minute delayed | 10 years |
| Stocks Advanced | $199/mo | Unlimited | **Real-time** | All history |

Massive additionally asks that even unlimited plans stay under ~100 req/s.

### Entitlement matrix for the endpoints we might use

| Endpoint | Basic (free) | Starter | Developer | Advanced |
|---|---|---|---|---|
| `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` — **Daily Market Summary** | ✅ EOD | ✅ | ✅ | ✅ |
| `GET /v2/aggs/ticker/{t}/prev` — Previous Day Bar | ✅ EOD | ✅ | ✅ | ✅ |
| `GET /v2/aggs/ticker/{t}/range/...` — Custom Bars | ✅ EOD | ✅ | ✅ | ✅ |
| `GET /v1/marketstatus/now` — Market Status | ✅ real-time | ✅ | ✅ | ✅ |
| `GET /v2/snapshot/locale/us/markets/stocks/tickers` — **Full Market Snapshot** | ❌ **Not included** | ✅ | ✅ | ✅ |
| `GET /v2/snapshot/.../tickers/{t}` — Single Ticker Snapshot | ❌ Not included | ✅ | ✅ | ✅ |
| `GET /v3/snapshot` — Unified Snapshot | ❌ Not included | ✅ | ✅ | ✅ |
| `WS /stocks/A` — per-second aggregates | ❌ Not included | ✅ | ✅ | ✅ |

**Consequences for FinAlly:**

1. A free key **cannot** use snapshots. It gets a `403`, not a `401`, and the difference
   matters: a 403 means "wrong plan, retrying will never help" — do not burn the backoff
   budget on it, switch strategy immediately.
2. A free key is capped at **5 calls/min**, i.e. one call every 12 seconds at best. That
   rules out per-ticker polling entirely: 10 watchlist tickers × `/prev` = 10 calls per
   round, over budget on the first poll.
3. Therefore the free-tier path **must** be the grouped daily bar endpoint: **one call
   returns every US ticker's daily OHLC.** Poll it once a minute (1 of 5 allowed calls)
   and you have prices for the whole watchlist plus any ticker the LLM adds later, for
   free, forever.
4. On a free key the data is **end-of-day**. Prices will not move. This is exactly why
   PLAN.md makes the simulator the default and says setting `MASSIVE_API_KEY` makes the
   data *real*, not *better*.

---

## 4. The endpoints, in detail

### 4.1 Daily Market Summary (grouped daily bars) — the free-tier workhorse

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}
```

| Param | Where | Notes |
|---|---|---|
| `date` | path | `YYYY-MM-DD`. Must be a **trading day** — a weekend/holiday returns `resultsCount: 0`. |
| `adjusted` | query | default `true` (split-adjusted). Keep the default. |
| `include_otc` | query | default `false`. Keep the default. |

One request, ~10,000 results, ~1–3 MB of JSON.

```json
{
  "adjusted": true,
  "queryCount": 3,
  "request_id": "...",
  "results": [
    { "T": "VSAT", "c": 34.24, "h": 35.47, "l": 34.21, "n": 4966,
      "o": 34.9, "t": 1602705600000, "v": 312583, "vw": 34.4736 }
  ],
  "resultsCount": 3,
  "status": "OK"
}
```

Field key (same short names across every aggregate endpoint):

| Field | Meaning |
|---|---|
| `T` | ticker (present on *grouped* results only) |
| `o` `h` `l` `c` | open / high / low / close |
| `v` | volume · `vw` volume-weighted average price · `n` transaction count |
| `t` | Unix **milliseconds**. Start of window on custom bars; end of window on grouped bars. |
| `otc` | present and `true` only for OTC rows |

**Picking `{date}`.** There is no "latest" alias. Walk backwards from today in US/Eastern
until a request returns `resultsCount > 0`, capped at ~5 attempts to cover a long weekend,
then cache the resolved date for the rest of the session. Do **not** re-resolve on every
poll — on a free key that alone would blow the 5/min budget. `GET /v1/marketstatus/now`
(free, real-time) gives you `serverTime` in Eastern, which is the right clock to start from.

### 4.2 Full Market Snapshot — the paid-tier workhorse

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
```

`tickers` is a **case-sensitive** comma-separated list; omitting it returns all ~10k
tickers. FinAlly always passes the tracked set (capped at 30 by the watchlist rules plus
held-but-unwatched positions), so one call per poll covers everything.

```json
{
  "count": 1,
  "status": "OK",
  "tickers": [{
    "ticker": "BCAT",
    "day":      { "o": 20.64, "h": 20.64, "l": 20.506, "c": 20.506, "v": 37216, "vw": 20.616 },
    "min":      { "o": 20.506, "h": 20.506, "l": 20.506, "c": 20.506, "v": 5000,
                  "av": 37216, "n": 1, "t": 1684428600000, "vw": 20.5105 },
    "prevDay":  { "o": 20.79, "h": 21, "l": 20.5, "c": 20.63, "v": 292738, "vw": 20.6939 },
    "lastTrade":{ "p": 20.506, "s": 2416, "t": 1605192894630916600, "x": 4, "c": [14, 41], "i": "..." },
    "lastQuote":{ "p": 20.5, "s": 13, "P": 20.6, "S": 22, "t": 1605192959994246100 },
    "todaysChange": -0.124,
    "todaysChangePerc": -0.601,
    "updated": 1605192894630916600
  }]
}
```

Mapping onto FinAlly's SSE payload (PLAN.md §6):

| FinAlly field | Source |
|---|---|
| `price` | `lastTrade.p` → fall back to `min.c` → `day.c` → `prevDay.c` |
| `open` (session open) | `day.o`, falling back to `prevDay.c` when `day.o` is 0 |
| `change` / `change_pct` | `todaysChange` / `todaysChangePerc` — **use these directly**, they are already computed against the previous close |
| `prev_price` / `direction` | **Not from the API.** Computed by our price cache by comparing to the last value we stored. |
| `ts` | `updated`, which is **Unix nanoseconds** — divide by 1e6 for ms |

**Timestamp units are not uniform.** `t` inside `day`/`min` aggregates is **milliseconds**;
`updated`, `lastTrade.t` and `lastQuote.t` are **nanoseconds**. Getting this wrong yields
timestamps in the year 52,000. Normalise on ingest.

**Pre-market / after-hours / cleared state.** Snapshot data is cleared daily at 3:30 AM ET
and repopulates from ~4:00 AM ET. Between those times `day` is zero-filled and
`todaysChangePerc` is meaningless. The fallback chain above (`day.c` → `prevDay.c`) is what
keeps the UI from showing $0.00 at 3:45 AM.

**Missing tickers are simply absent** from the `tickers` array — there is no per-ticker
error object here. Requesting a symbol that does not exist silently returns fewer results.
Diff the response against the requested set and leave unknown tickers on their cached value.

### 4.3 Previous Day Bar — for the initial session-open reference

```
GET /v2/aggs/ticker/{stocksTicker}/prev?adjusted=true
```

Free on every plan, but **one ticker per call**. Use it only for a one-off backfill of a
single newly-added ticker, never in the poll loop.

```json
{ "ticker": "AAPL", "adjusted": true, "queryCount": 1, "resultsCount": 1, "status": "OK",
  "results": [{ "T": "AAPL", "o": 115.55, "h": 117.59, "l": 114.13, "c": 115.97,
                "t": 1605042000000, "v": 131704427, "vw": 116.3058 }] }
```

### 4.4 Custom Bars — for seeding charts

```
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
    ?adjusted=true&sort=asc&limit=600
```

`timespan` ∈ `second|minute|hour|day|week|month|quarter|year`. `from`/`to` are `YYYY-MM-DD`
or epoch-ms, interpreted in **Eastern Time**. `limit` caps *base* aggregates (max 50,000,
default 5,000) and the response may carry `next_url`.

FinAlly's `GET /api/history/{ticker}` is served from the in-memory ring buffer, not from
here — so this endpoint is **optional**. Its one good use: on a Massive-backed run the ring
buffer starts empty, and one call per ticker of `1/minute` bars for the last trading day
gives the chart a real intraday shape instead of a flat line. Gate it behind a "backfill on
first sight of a ticker" path so it costs one call per ticker per process, not per poll.

Bars are built only from qualifying trades: **a minute with no trades produces no bar**, so
the series has gaps. Do not assume evenly spaced points.

### 4.5 Market Status

```
GET /v1/marketstatus/now
```

Free, real-time, on every plan.

```json
{ "market": "extended-hours", "earlyHours": false, "afterHours": true,
  "exchanges": { "nasdaq": "extended-hours", "nyse": "extended-hours", "otc": "closed" },
  "currencies": { "crypto": "open", "fx": "open" },
  "serverTime": "2020-11-10T17:37:37-05:00" }
```

`market` ∈ `open | closed | extended-hours`. Two uses in FinAlly: resolving the latest
trading date (§4.1), and — worth surfacing — telling the user *why* prices are frozen when
the market is closed, rather than letting them conclude the app is broken.

### 4.6 Unified Snapshot (`GET /v3/snapshot`) — noted, not used

Multi-asset, `ticker.any_of` up to 250 symbols, a richer `session` object with
`previous_close`, and — unlike §4.2 — **per-ticker error rows**:

```json
{ "error": "NOT_FOUND", "message": "Ticker not found.", "ticker": "TSLAAPL" }
```

Same entitlement as §4.2 (Starter+), and default `limit` is **10** — you must pass
`limit=250` or you will silently get ten results. We stay on the v2 snapshot because it is
one fewer parameter to get wrong and returns the same prices. Reach for v3 only if we ever
want explicit "unknown ticker" feedback from the upstream.

---

## 5. The strategy FinAlly implements

Two request shapes, chosen by probing entitlement once at startup:

```
                       ┌─ MASSIVE_API_KEY unset/empty ──► Simulator (default path)
  startup ─────────────┤
                       └─ key set ──► probe: GET /v2/snapshot/...?tickers=AAPL
                                        │
                            200 ────────┼──► SNAPSHOT mode
                            403 ────────┼──► GROUPED mode  (free tier)
                            401 ────────┼──► log "bad key" ──► Simulator, degraded
                            5xx/timeout ┴──► retry w/ backoff; 3 strikes ──► Simulator, degraded
```

| | SNAPSHOT mode | GROUPED mode |
|---|---|---|
| Endpoint | `/v2/snapshot/locale/us/markets/stocks/tickers?tickers=…` | `/v2/aggs/grouped/locale/us/market/stocks/{date}` |
| Plan | Starter and above | Basic (free) and above |
| Poll interval | **15 s** (2–5 s on Advanced) | **60 s** |
| Calls / min | 4 | 1 of the 5 allowed |
| Covers | exactly the tracked set | every US ticker — new watchlist entries need no extra call |
| Recency | 15-min delayed, or real-time on Advanced | end-of-day: **prices will not change** |
| Response size | a few KB | 1–3 MB — parse and discard, keep only tracked tickers |

Both modes emit the same `Quote` objects into the shared price cache, so nothing downstream
knows which one ran. The failure ladder (§6) is identical for both.

**GROUPED mode is honest but static.** On a free key, every tick has the same price, so
`direction` is `flat` and no flash animation ever fires. The right product answer is to
mark the stream **`degraded`** (yellow dot, per PLAN.md §10) with a reason string the
frontend can show on hover: *"end-of-day data — free Massive tier"*. Do not fake movement.

---

## 6. Failure handling

| Status | Meaning | Correct reaction |
|---|---|---|
| `200` | OK — but check `"status": "OK"` in the body too | consume |
| `401` | `API Key was not provided` / `Unknown API Key` | **Terminal.** Log once, fall back to the simulator, mark `degraded`. Never retry — the key will not fix itself. |
| `403` | Plan does not include this endpoint (`NOT_AUTHORIZED`) | **Terminal for that endpoint.** Do not back off; switch SNAPSHOT → GROUPED immediately. |
| `429` | Rate limited (free tier's 5/min) | Back off exponentially, cap 60 s. Also *increase the poll interval* — a 429 means the interval is wrong, not that this one request was unlucky. |
| `5xx` | Upstream problem | Retry with exponential backoff, cap 60 s. |
| timeout / `ConnectError` | Network | Same as 5xx. |

Per PLAN.md §6: keep the last cached prices across all of these, and **after three
consecutive failures fall back to the simulator for the remainder of the process lifetime**
and mark the stream `degraded`. **The SSE stream must never die because the upstream did.**

A caution on the `403` body: the 401 shapes in §2 are verified; the 403 shape
(`{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data..."}`) is
reported rather than verified here, because it needs a real free-tier key to observe.
**Branch on the HTTP status code, not on the body text.**

---

## 7. Reference implementation (httpx, async)

Sketch for `backend/app/market/massive.py`. The production version implementing
`MarketDataSource` is specified in `MARKET_INTERFACE.md`.

```python
"""Massive (formerly Polygon.io) REST market data client."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.massive.com"
EASTERN = timezone(timedelta(hours=-5))  # good enough for date resolution; see note below


class MassiveAuthError(RuntimeError):
    """401 — the key is missing or wrong. Terminal; do not retry."""


class MassiveEntitlementError(RuntimeError):
    """403 — the plan does not cover this endpoint. Terminal for this endpoint."""


@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    price: float
    session_open: float
    ts: datetime


class MassiveClient:
    def __init__(self, api_key: str, *, base_url: str = MASSIVE_BASE_URL) -> None:
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
        if resp.status_code == 401:
            raise MassiveAuthError(f"{path}: bad or missing API key (request_id={rid})")
        if resp.status_code == 403:
            raise MassiveEntitlementError(f"{path}: not entitled on this plan (request_id={rid})")
        resp.raise_for_status()          # 429 and 5xx become HTTPStatusError -> backoff
        body = resp.json()
        if body.get("status") not in (None, "OK", "DELAYED"):
            log.warning("massive: %s returned status=%s request_id=%s",
                        path, body.get("status"), rid)
        return body

    # ---- SNAPSHOT mode (Starter and above) --------------------------------

    async def snapshot(self, tickers: set[str]) -> list[Quote]:
        """One call for the whole tracked set."""
        if not tickers:
            return []
        body = await self._get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            tickers=",".join(sorted(tickers)),
        )
        quotes: list[Quote] = []
        for row in body.get("tickers", []):
            q = _parse_snapshot_row(row)
            if q is not None:
                quotes.append(q)
        missing = tickers - {q.ticker for q in quotes}
        if missing:
            log.debug("massive: no snapshot rows for %s", sorted(missing))
        return quotes

    # ---- GROUPED mode (free tier) ----------------------------------------

    async def grouped_daily(self, date: str, tickers: set[str]) -> list[Quote]:
        """One call returns every US ticker; we keep only the ones we track."""
        body = await self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{date}")
        ts = datetime.now(timezone.utc)
        return [
            Quote(ticker=r["T"], price=float(r["c"]),
                  session_open=float(r.get("o") or r["c"]), ts=ts)
            for r in body.get("results", ())
            if r.get("T") in tickers and r.get("c")
        ]

    async def latest_trading_date(self, *, max_lookback: int = 5) -> str:
        """Walk back from 'today in ET' to the most recent date with data.

        Call this ONCE per process and cache it — on the free tier it costs
        one of only five requests per minute.
        """
        day = datetime.now(EASTERN).date()
        for _ in range(max_lookback):
            body = await self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}")
            if body.get("resultsCount", 0) > 0:
                return day.isoformat()
            day -= timedelta(days=1)
        raise RuntimeError(f"no trading data in the last {max_lookback} days")

    async def market_status(self) -> dict:
        return await self._get("/v1/marketstatus/now")


def _ns_to_dt(value: int | None) -> datetime:
    """`updated` / `lastTrade.t` are NANOseconds; `day.t` / `min.t` are MILLIseconds."""
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _parse_snapshot_row(row: dict) -> Quote | None:
    """Fall through last trade -> minute bar -> day bar -> previous close.

    Between 3:30 and 4:00 AM ET the snapshot is cleared and `day` is zero-filled,
    so every one of these fallbacks earns its place.
    """
    day, minute, prev = row.get("day") or {}, row.get("min") or {}, row.get("prevDay") or {}
    price = (
        (row.get("lastTrade") or {}).get("p")
        or minute.get("c") or day.get("c") or prev.get("c")
    )
    if not price:
        return None
    return Quote(
        ticker=row["ticker"],
        price=float(price),
        session_open=float(day.get("o") or prev.get("c") or price),
        ts=_ns_to_dt(row.get("updated")),
    )
```

**On `EASTERN` as a fixed −5 offset:** that is EST, and it is wrong by an hour during
daylight saving. It only ever shifts which *date* we probe, and `latest_trading_date`
already walks backwards until it finds data, so the error self-corrects at the cost of one
extra request. If you want it exact, use `zoneinfo.ZoneInfo("America/New_York")` — stdlib,
no dependency — or take `serverTime` from `/v1/marketstatus/now`.

### Smoke test

```bash
export MASSIVE_API_KEY=...    # from https://massive.com/dashboard/keys

# 1. Is the key valid at all? (free on every plan)
curl -s -H "Authorization: Bearer $MASSIVE_API_KEY" \
  "https://api.massive.com/v2/aggs/ticker/AAPL/prev" | jq '.status, .results[0].c'

# 2. Which mode does this key get? 200 => SNAPSHOT, 403 => GROUPED.
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $MASSIVE_API_KEY" \
  "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT"

# 3. Is the market even open? (explains a frozen UI)
curl -s -H "Authorization: Bearer $MASSIVE_API_KEY" \
  "https://api.massive.com/v1/marketstatus/now" | jq '.market, .serverTime'
```

Ship step 2 as a `backend/scripts/check_massive.py` so a student with a key can find out in
five seconds which tier they are on.

---

## 8. Official SDK (cross-reference only)

```bash
pip install -U massive     # Python >= 3.9
```

```python
from massive import RESTClient

client = RESTClient(api_key="<API_KEY>")                 # trace=True, verbose=True to debug
snap  = client.get_snapshot_all("stocks", ["AAPL", "MSFT"])
prev  = client.get_previous_close_agg("AAPL")
aggs  = list(client.list_aggs("AAPL", 1, "minute", "2026-08-27", "2026-08-28", limit=50000))
```

Pagination is automatic by default (`limit` is the *page* size); pass `pagination=False` to
make `limit` a hard cap. Useful when you want to confirm a field name without writing a
parser — but **not** what FinAlly ships, for the reasons in §1.

---

## 9. Why REST polling and not WebSocket

PLAN.md already specifies REST polling. The research backs it up:

- The stocks WebSocket feeds are **Starter and above** — same exclusion as snapshots, so
  they solve nothing for the free-tier user we are actually designing for.
- A WebSocket adds a second long-lived connection with its own reconnect, auth, and
  subscribe/unsubscribe lifecycle, on top of the SSE connection we already maintain to the
  browser. Two failure modes for one price feed.
- The tracked set changes at runtime (the LLM edits the watchlist), which means
  incremental `subscribe`/`unsubscribe` messages and reconciliation. A REST poll just sends
  the current set every time — the set *is* the request.
- We poll at 15 s and stream to the browser at 500 ms. Sub-second upstream latency buys
  nothing a user of a simulated $10,000 portfolio can perceive.

---

## 10. Facts, and how confident we are in them

| Claim | Basis |
|---|---|
| `api.massive.com` is live; both auth methods work; 401 bodies as quoted in §2 | **Verified** by live request, 2026-08-28 |
| Endpoint paths, parameters, response shapes, entitlement and recency tables | Massive docs (`https://massive.com/docs/*.md`), read 2026-08-28 |
| Free tier = 5 req/min, EOD, 2 years history | Massive knowledge base + pricing page |
| SDK package `massive` 2.8.0; `polygon-api-client` 1.16.3 | PyPI, 2026-08-28 |
| 403 response *body* shape | Reported, **not verified** — branch on status code (§6) |
| Grouped-daily response size (~1–3 MB) | Estimate from ~10k rows; measure once a key is available |

**Sources**

- [Massive API docs](https://massive.com/docs) · [machine-readable index](https://massive.com/docs/llms.txt)
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot) · [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary) · [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar) · [Custom Bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars) · [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot) · [Market Status](https://massive.com/docs/rest/stocks/market-operations/market-status)
- [Request limits](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis) · [Pricing](https://massive.com/pricing) · [Rebrand announcement](https://massive.com/blog/polygon-is-now-massive)
- [massive-com/client-python](https://github.com/massive-com/client-python) · [`massive` on PyPI](https://pypi.org/project/massive/)
