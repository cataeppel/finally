# backend/app/market/types.py
"""Value types shared by every part of the market module.

`Quote` is what a SOURCE produces. `Tick` is what the CACHE derives and what the
SSE stream emits. Keeping them separate is the whole reason a source can be swapped
at runtime: a source knows a price, it does not know the previous price, so it
cannot compute `direction`. Every derived field is computed in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class StreamStatus(str, Enum):
    """Drives the connection dot in the header (PLAN.md §10)."""

    CONNECTED = "connected"        # green  — live source, healthy
    DEGRADED = "degraded"          # yellow — fell back to the simulator, or EOD-only data
    DISCONNECTED = "disconnected"  # red    — no producer running


@dataclass(frozen=True, slots=True)
class Quote:
    """One observation from a source. The ONLY thing a source ever produces.

    `session_open` is the source's own reference for the daily-change column.
    Massive supplies the real session open (`day.o`); the simulator supplies the
    first price it minted this process. `None` means "no opinion" — the cache then
    pins the session open to the first price it sees.
    """

    ticker: str
    price: float
    ts: datetime                    # timezone-aware UTC
    session_open: float | None = None


@dataclass(frozen=True, slots=True)
class Tick:
    """A fully derived price event. Serialises 1:1 to the SSE payload in PLAN.md §6."""

    ticker: str
    price: float
    prev_price: float
    open: float
    change: float                   # price - open
    change_pct: float               # 100 * change / open
    direction: str                  # "up" | "down" | "flat", vs. prev_price
    ts: datetime

    def to_payload(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 4),
            "prev_price": round(self.prev_price, 4),
            "open": round(self.open, 4),
            "change": round(self.change, 4),
            "change_pct": round(self.change_pct, 4),
            "direction": self.direction,
            "ts": iso_z(self.ts),
        }


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One entry in a ring buffer. Backs GET /api/history/{ticker}."""

    ts: datetime
    price: float

    def to_payload(self) -> dict:
        return {"ts": iso_z(self.ts), "price": round(self.price, 4)}


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601, millisecond precision, 'Z' suffix — PLAN.md §7 conventions.

    One formatter, used by every timestamp the backend emits, so lexicographic order
    always equals chronological order and the frontend has exactly one format to parse.

    >>> iso_z(datetime(2026, 8, 26, 14, 3, 7, 412_000, tzinfo=timezone.utc))
    '2026-08-26T14:03:07.412Z'
    """
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def utcnow() -> datetime:
    """The module's only clock. Patch this in tests rather than datetime itself."""
    return datetime.now(timezone.utc)
