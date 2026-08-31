"""Deterministic mock LLM responses for testing (LLM_MOCK=true).

Used by E2E tests and by anyone running without an OPENROUTER_API_KEY. Given
the same message and portfolio context, `mock_chat` always returns the same
`LlmResponse` — it never calls the network.

Supported triggers (matched in this order, case-insensitive):

    "buy 10 AAPL" / "buy 10 shares of AAPL"     -> executes a buy
    "sell 10 AAPL" / "sell 10 shares of AAPL"   -> executes a sell
    "add PYPL to my watchlist" / "watch PYPL"   -> watchlist add
    "remove AAPL from my watchlist" /
        "unwatch AAPL" / "stop watching AAPL"   -> watchlist remove
    anything containing portfolio / positions /
        holdings / analyze / analysis / p&l     -> portfolio summary
    anything else                               -> greeting

Trades and watchlist changes still go through the real validation path, so an
unaffordable mock buy produces a genuine "Insufficient cash" error — which is
how E2E tests exercise the failure path (e.g. "buy 100000 AAPL").
"""

import re

from .models import LlmResponse, TradeAction, WatchlistChange

_QTY = r"(\d+(?:\.\d+)?)"
_TICKER = r"([A-Za-z][A-Za-z0-9.\-]{0,9})"

_BUY_RE = re.compile(rf"\bbuy\s+{_QTY}\s+(?:shares?\s+(?:of\s+)?)?{_TICKER}\b", re.I)
_SELL_RE = re.compile(rf"\bsell\s+{_QTY}\s+(?:shares?\s+(?:of\s+)?)?{_TICKER}\b", re.I)

_WATCH_ADD_RE = re.compile(
    rf"(?:\bwatch\s+{_TICKER}\b|\badd\s+{_TICKER}\s+to\s+(?:my\s+|the\s+)?watchlist\b)", re.I
)
_WATCH_REMOVE_RE = re.compile(
    rf"(?:\b(?:unwatch|stop\s+watching)\s+{_TICKER}\b"
    rf"|\bremove\s+{_TICKER}\s+from\s+(?:my\s+|the\s+)?watchlist\b)",
    re.I,
)

_PORTFOLIO_KEYWORDS = ("portfolio", "positions", "holdings", "analyze", "analysis", "p&l", "pnl")

GREETING = (
    "I'm FinAlly, your AI trading assistant. I can analyze your portfolio, "
    "execute trades, and manage your watchlist. How can I help?"
)


def _first_group(match: re.Match) -> str:
    """The first non-None capture group — the alternations capture in different slots."""
    return next(g for g in match.groups() if g is not None)


def _format_quantity(quantity: float) -> str:
    return f"{quantity:g}"


def mock_chat(user_message: str, context: dict) -> LlmResponse:
    """Return a deterministic response for `user_message` given the portfolio context."""
    msg = (user_message or "").strip()
    lowered = msg.lower()

    buy = _BUY_RE.search(msg)
    if buy:
        quantity = float(buy.group(1))
        ticker = buy.group(2).upper()
        return LlmResponse(
            message=f"Executing purchase of {_format_quantity(quantity)} shares of {ticker}.",
            trades=[TradeAction(ticker=ticker, side="buy", quantity=quantity)],
        )

    sell = _SELL_RE.search(msg)
    if sell:
        quantity = float(sell.group(1))
        ticker = sell.group(2).upper()
        return LlmResponse(
            message=f"Executing sale of {_format_quantity(quantity)} shares of {ticker}.",
            trades=[TradeAction(ticker=ticker, side="sell", quantity=quantity)],
        )

    # Removal is checked before addition so "remove X from my watchlist" is not
    # mistaken for an add by the bare "watch X" alternative.
    remove = _WATCH_REMOVE_RE.search(msg)
    if remove:
        ticker = _first_group(remove).upper()
        return LlmResponse(
            message=f"Removing {ticker} from your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="remove")],
        )

    add = _WATCH_ADD_RE.search(msg)
    if add:
        ticker = _first_group(add).upper()
        return LlmResponse(
            message=f"Adding {ticker} to your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="add")],
        )

    if any(keyword in lowered for keyword in _PORTFOLIO_KEYWORDS):
        cash = context.get("cash", 0.0)
        positions = context.get("positions") or []
        total_value = context.get("total_value", cash)

        if positions:
            tickers = ", ".join(p["ticker"] for p in positions)
            return LlmResponse(
                message=(
                    f"Your portfolio is worth ${total_value:,.2f} with "
                    f"${cash:,.2f} in cash. You hold: {tickers}."
                ),
            )
        return LlmResponse(
            message=(
                f"You have ${cash:,.2f} in cash and no open positions. "
                "Consider starting with a diversified set of holdings."
            ),
        )

    return LlmResponse(message=GREETING)
