# backend/app/market/cache.py
"""In-memory price state. Single-process, never persisted, rebuilt from scratch on
restart — which is why the P&L chart shows straight segments across a container
restart (PLAN.md §7) and why /api/history starts empty.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .types import PricePoint, Quote, Tick

RING_SIZE = 600     # PLAN.md §6: ~5 minutes at the simulator's 500 ms cadence


@dataclass(slots=True)
class TickerState:
    ticker: str
    price: float
    prev_price: float
    session_open: float
    updated_at: datetime
    ring: deque[PricePoint] = field(default_factory=lambda: deque(maxlen=RING_SIZE))


class PriceCache:
    def __init__(self) -> None:
        self._states: dict[str, TickerState] = {}

    def apply(self, quote: Quote) -> Tick | None:
        """Fold one quote into state and return the Tick to broadcast.

        Returns None when the quote carries no new information — an identical price
        at or before the timestamp we already hold. Callers MUST handle None: on a
        free-tier Massive key every quote after the first is a repeat.
        """
        st = self._states.get(quote.ticker)
        if st is None:
            open_ = quote.session_open if quote.session_open is not None else quote.price
            st = TickerState(
                ticker=quote.ticker,
                price=quote.price,
                prev_price=quote.price,      # the first tick is flat, by definition
                session_open=open_,
                updated_at=quote.ts,
            )
            self._states[quote.ticker] = st
        else:
            if quote.price == st.price and quote.ts <= st.updated_at:
                return None
            st.prev_price = st.price
            st.price = quote.price
            st.updated_at = quote.ts
            # A source may learn the real session open late (a Massive backfill);
            # accept a real value, never let a None drift it back.
            if quote.session_open is not None:
                st.session_open = quote.session_open

        st.ring.append(PricePoint(ts=quote.ts, price=quote.price))
        return _derive_tick(st)

    def get(self, ticker: str) -> TickerState | None:
        return self._states.get(ticker)

    def price(self, ticker: str) -> float | None:
        st = self._states.get(ticker)
        return st.price if st else None

    def snapshot(self) -> dict[str, Tick]:
        """Every tracked ticker's current tick — used to prime a new SSE subscriber."""
        return {t: _derive_tick(st) for t, st in self._states.items()}

    def history(self, ticker: str) -> list[PricePoint]:
        st = self._states.get(ticker)
        return list(st.ring) if st else []

    def evict(self, keep: frozenset[str]) -> None:
        for t in list(self._states):
            if t not in keep:
                del self._states[t]


def _derive_tick(st: TickerState) -> Tick:
    open_ = st.session_open or st.price          # never divide by zero
    change = st.price - open_
    if st.price > st.prev_price:
        direction = "up"
    elif st.price < st.prev_price:
        direction = "down"
    else:
        direction = "flat"
    return Tick(
        ticker=st.ticker,
        price=st.price,
        prev_price=st.prev_price,
        open=open_,
        change=change,
        change_pct=(change / open_ * 100.0) if open_ else 0.0,
        direction=direction,
        ts=st.updated_at,
    )
