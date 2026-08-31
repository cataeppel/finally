"""LLM chat service — calls OpenRouter/Cerebras and executes the actions it returns.

Flow (PLAN.md §9): build portfolio context, load conversation history, call the
model with structured outputs, parse, auto-execute trades and watchlist changes
through the same validation path as manual trades, and return the message plus
per-action results (including validation errors) to the caller.
"""

import asyncio
import logging
import os

from litellm import completion

from app.db import (
    DuplicateTickerError,
    add_to_watchlist,
    get_chat_history,
    get_watchlist,
    remove_from_watchlist,
)
from app.market import PriceCache
from app.routes.trading import (
    TradeError,
    execute_trade_order,
    normalize_ticker,
    value_portfolio,
)

from .mock import mock_chat
from .models import LlmResponse
from .parsing import LlmParseError, parse_llm_response

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}

HISTORY_LIMIT = 20

ERROR_MESSAGE = (
    "Sorry, I couldn't reach the trading assistant just now. Please try again."
)
NO_KEY_MESSAGE = (
    "The AI assistant isn't configured — OPENROUTER_API_KEY is not set. "
    "Add it to your .env file (or set LLM_MOCK=true) and restart."
)

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant for a simulated trading workstation.

You help the user manage a virtual stock portfolio. You can:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with clear reasoning
- Execute trades when the user asks or agrees (buy or sell shares)
- Add or remove tickers from the watchlist, proactively when it helps

Rules:
- This is a simulation with virtual money — no real orders are placed
- All orders are market orders, filled instantly at the current price, no fees
- Only include a trade in `trades` when the user has asked for it or agreed to it;
  a suggestion you are merely proposing belongs in `message`, not in `trades`
- Never trade more than the cash or shares shown in the portfolio state below
- Be concise and data-driven; cite the actual numbers you were given
- Always respond with valid JSON matching the required schema"""


def mock_mode_enabled() -> bool:
    """True when LLM_MOCK is set to a truthy value."""
    return os.environ.get("LLM_MOCK", "").strip().lower() in ("1", "true", "yes")


async def _build_context(price_cache: PriceCache) -> dict:
    """Build the portfolio context included in the LLM prompt.

    Valuation comes from the shared `value_portfolio` so the numbers the model
    sees are exactly the ones GET /api/portfolio reports.
    """
    valuation = await value_portfolio(price_cache)
    watchlist = await get_watchlist()

    watchlist_with_prices = []
    for entry in watchlist:
        price = price_cache.get_price(entry["ticker"])
        watchlist_with_prices.append({
            "ticker": entry["ticker"],
            "price": round(price, 2) if price is not None else None,
        })

    return {
        "cash": valuation["cash"],
        "positions": valuation["positions"],
        "watchlist": watchlist_with_prices,
        "total_value": valuation["total_value"],
        "total_market_value": valuation["total_market_value"],
        "unrealized_pnl": valuation["unrealized_pnl"],
    }


def _format_context(context: dict) -> str:
    """Render the portfolio context as compact text for the system message."""
    lines = [
        f"Cash available: ${context['cash']:,.2f}",
        f"Total portfolio value: ${context['total_value']:,.2f} "
        f"(positions ${context['total_market_value']:,.2f})",
    ]

    if context["positions"]:
        lines.append("Positions:")
        for p in context["positions"]:
            lines.append(
                f"  {p['ticker']}: {p['quantity']:g} shares @ avg ${p['avg_cost']:.2f}, "
                f"now ${p['current_price']:.2f}, value ${p['market_value']:,.2f}, "
                f"P&L ${p['unrealized_pnl']:+,.2f} ({p['pnl_percent']:+.2f}%)"
            )
    else:
        lines.append("Positions: none — the portfolio is all cash.")

    if context["watchlist"]:
        quoted = ", ".join(
            f"{w['ticker']} ${w['price']:.2f}" if w["price"] is not None else f"{w['ticker']} (no quote)"
            for w in context["watchlist"]
        )
        lines.append(f"Watchlist: {quoted}")
    else:
        lines.append("Watchlist: empty")

    return "\n".join(lines)


def _trim_history(history: list[dict], user_message: str) -> list[dict]:
    """Drop the just-persisted copy of the incoming message.

    The chat route stores the user's message before calling us, so the history
    it loads already ends with that message. Appending it again would show the
    model the same turn twice.
    """
    trimmed = list(history)
    if (
        trimmed
        and trimmed[-1].get("role") == "user"
        and trimmed[-1].get("content") == user_message
    ):
        trimmed.pop()
    return trimmed


def _build_messages(context: dict, history: list[dict], user_message: str) -> list[dict]:
    """Construct the messages list for the LLM call."""
    messages = [{
        "role": "system",
        "content": f"{SYSTEM_PROMPT}\n\nCurrent portfolio state:\n{_format_context(context)}",
    }]

    for msg in _trim_history(history, user_message)[-HISTORY_LIMIT:]:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})
    return messages


async def _execute_actions(llm_response: LlmResponse, price_cache: PriceCache) -> dict:
    """Execute trades and watchlist changes, collecting per-action results.

    Failures never raise — they come back as `error` entries so the chat
    response can tell the user exactly what went wrong.
    """
    trade_results = []
    for trade in llm_response.trades:
        ticker = (trade.ticker or "").strip().upper()
        side = (trade.side or "").strip().lower()
        try:
            result = await execute_trade_order(
                trade.ticker, trade.side, trade.quantity, price_cache
            )
        except TradeError as exc:
            trade_results.append({
                "ticker": ticker,
                "side": side,
                "quantity": trade.quantity,
                "error": str(exc),
            })
            continue
        except Exception:
            logger.exception("Unexpected failure executing LLM trade %s %s", side, ticker)
            trade_results.append({
                "ticker": ticker,
                "side": side,
                "quantity": trade.quantity,
                "error": "Trade failed unexpectedly",
            })
            continue

        trade_results.append({
            "ticker": result["trade"]["ticker"],
            "side": result["trade"]["side"],
            "quantity": result["trade"]["quantity"],
            "price": result["trade"]["price"],
            "status": "executed",
        })

    watchlist_results = []
    existing = None
    for change in llm_response.watchlist_changes:
        raw_ticker = (change.ticker or "").strip().upper()
        action = (change.action or "").strip().lower()
        try:
            ticker = normalize_ticker(change.ticker)
        except TradeError as exc:
            watchlist_results.append({"ticker": raw_ticker, "action": action, "error": str(exc)})
            continue

        if existing is None:
            existing = {w["ticker"] for w in await get_watchlist()}

        if action == "add":
            if ticker in existing:
                watchlist_results.append({
                    "ticker": ticker, "action": "add", "error": f"{ticker} is already on the watchlist",
                })
                continue
            try:
                await add_to_watchlist(ticker)
            except DuplicateTickerError:
                watchlist_results.append({
                    "ticker": ticker, "action": "add", "error": f"{ticker} is already on the watchlist",
                })
                continue
            except Exception:
                logger.exception("Failed adding %s to watchlist", ticker)
                watchlist_results.append({
                    "ticker": ticker, "action": "add", "error": f"Could not add {ticker} to the watchlist",
                })
                continue
            existing.add(ticker)
            watchlist_results.append({"ticker": ticker, "action": "add", "status": "done"})

        else:  # remove
            if await remove_from_watchlist(ticker):
                existing.discard(ticker)
                watchlist_results.append({"ticker": ticker, "action": "remove", "status": "done"})
            else:
                watchlist_results.append({
                    "ticker": ticker, "action": "remove", "error": f"{ticker} is not on the watchlist",
                })

    return {"trades": trade_results, "watchlist_changes": watchlist_results}


def _call_model(messages: list[dict]) -> str | None:
    """Blocking LiteLLM call — run via asyncio.to_thread so the loop stays free."""
    response = completion(
        model=MODEL,
        messages=messages,
        response_format=LlmResponse,
        reasoning_effort="low",
        extra_body=EXTRA_BODY,
    )
    return response.choices[0].message.content


async def chat_with_llm(user_message: str, price_cache: PriceCache) -> dict:
    """Process a chat message: call the LLM (or the mock), execute actions, respond."""
    context = await _build_context(price_cache)

    if mock_mode_enabled():
        llm_response = mock_chat(user_message, context)
    else:
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            logger.error("OPENROUTER_API_KEY is not set and LLM_MOCK is not enabled")
            return {"message": NO_KEY_MESSAGE, "trades": [], "watchlist_changes": []}

        history = await get_chat_history(limit=HISTORY_LIMIT + 1)
        messages = _build_messages(context, history, user_message)

        try:
            content = await asyncio.to_thread(_call_model, messages)
        except Exception:
            # Never log the exception's request payload at a level that could
            # include the API key; litellm redacts it, and we only log the trace.
            logger.exception("LLM call failed")
            return {"message": ERROR_MESSAGE, "trades": [], "watchlist_changes": []}

        try:
            llm_response = parse_llm_response(content)
        except LlmParseError:
            logger.exception("Could not parse LLM response")
            return {
                "message": "I got a garbled response from the model. Please try rephrasing.",
                "trades": [],
                "watchlist_changes": [],
            }

    action_results = await _execute_actions(llm_response, price_cache)
    return {
        "message": llm_response.message,
        "trades": action_results["trades"],
        "watchlist_changes": action_results["watchlist_changes"],
    }
