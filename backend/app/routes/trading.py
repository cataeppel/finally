"""Shared trade execution and portfolio valuation logic.

Both the manual trade endpoint (`POST /api/portfolio/trade`) and the LLM chat
action executor must apply *identical* validation rules (PLAN.md §9). This
module owns that logic so the two paths cannot drift apart.

The order itself — cash, position and trade log — is applied atomically by
`app.db.apply_trade`; nothing here writes SQL.
"""

from __future__ import annotations

import re

from app.db import (
    InsufficientCashError,
    InsufficientSharesError,
    apply_trade,
    get_cash_balance,
    get_positions,
    get_watchlist_tickers,
    insert_snapshot,
)
from app.market import MarketDataSource, PriceCache

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class TradeError(ValueError):
    """A trade failed validation. The message is safe to show the user."""


def normalize_ticker(raw: str) -> str:
    """Uppercase and validate a ticker symbol.

    Raises TradeError if the symbol is empty or not a plausible ticker.
    """
    ticker = (raw or "").strip().upper()
    if not ticker:
        raise TradeError("Ticker is required")
    if not _TICKER_RE.match(ticker):
        raise TradeError(f"Invalid ticker symbol: {raw!r}")
    return ticker


def _price_for(cache: PriceCache, position: dict) -> float:
    """Latest price for a position, falling back to its average cost."""
    price = cache.get_price(position["ticker"])
    return price if price is not None else position["avg_cost"]


async def value_portfolio(cache: PriceCache) -> dict:
    """Value the whole portfolio at current cache prices.

    Returns cash, per-position detail, aggregate market value, total value and
    unrealized P&L. Used by GET /api/portfolio, the snapshot task and the
    post-trade snapshot so all three agree.
    """
    cash = await get_cash_balance()
    positions = await get_positions()

    detail = []
    total_market_value = 0.0
    total_unrealized_pnl = 0.0

    for pos in positions:
        current_price = _price_for(cache, pos)
        market_value = current_price * pos["quantity"]
        cost_basis = pos["avg_cost"] * pos["quantity"]
        unrealized_pnl = market_value - cost_basis
        pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0

        detail.append({
            "ticker": pos["ticker"],
            "quantity": pos["quantity"],
            "avg_cost": pos["avg_cost"],
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "pnl_percent": round(pnl_percent, 2),
        })

        total_market_value += market_value
        total_unrealized_pnl += unrealized_pnl

    return {
        "positions": detail,
        "cash": round(cash, 2),
        "total_market_value": round(total_market_value, 2),
        "total_value": round(cash + total_market_value, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
    }


async def record_snapshot(cache: PriceCache) -> float:
    """Record a portfolio_snapshots row at current prices. Returns total value."""
    valuation = await value_portfolio(cache)
    await insert_snapshot(valuation["total_value"])
    return valuation["total_value"]


async def execute_trade_order(
    ticker: str, side: str, quantity: float, cache: PriceCache
) -> dict:
    """Execute a market order at the current cached price.

    Validates the ticker, side, quantity and price availability, then hands the
    order to the repository, which checks available cash or held shares and
    applies cash, position and trade-log changes in one transaction. A portfolio
    snapshot is recorded afterwards (PLAN.md §7).

    Raises TradeError (with a user-facing message) on any validation failure;
    nothing is persisted in that case.
    """
    ticker = normalize_ticker(ticker)
    side = (side or "").strip().lower()

    if side not in ("buy", "sell"):
        raise TradeError("side must be 'buy' or 'sell'")
    # NaN compares false against everything, so test it before the range check.
    if quantity is None or quantity != quantity or quantity <= 0:
        raise TradeError("quantity must be positive")

    current_price = cache.get_price(ticker)
    if current_price is None:
        raise TradeError(
            f"No price available for {ticker}. Add it to your watchlist first."
        )

    try:
        result = await apply_trade(ticker, side, quantity, current_price)
    except (InsufficientCashError, InsufficientSharesError, ValueError) as exc:
        raise TradeError(str(exc)) from exc

    total_value = await record_snapshot(cache)

    return {
        "trade": result["trade"],
        "cash": round(result["cash"], 2),
        "total_value": total_value,
    }


async def tickers_to_track() -> list[str]:
    """Watchlist tickers plus any ticker with an open position.

    A held ticker needs a live price even if the user has dropped it from the
    watchlist, otherwise its position could never be valued or sold.
    """
    tickers = await get_watchlist_tickers()
    seen = set(tickers)
    for pos in await get_positions():
        if pos["ticker"] not in seen:
            seen.add(pos["ticker"])
            tickers.append(pos["ticker"])
    return tickers


async def sync_tracked_tickers(source: MarketDataSource, cache: PriceCache) -> None:
    """Reconcile the market data source with what the database says to track.

    Called after any watchlist or position change — including changes the LLM
    makes on its own — so a newly watched ticker starts streaming immediately
    and a dropped one stops.
    """
    desired = set(await tickers_to_track())
    current = set(source.get_tickers())

    for ticker in sorted(desired - current):
        await source.add_ticker(ticker)
    for ticker in sorted(current - desired):
        await source.remove_ticker(ticker)
        cache.remove(ticker)
