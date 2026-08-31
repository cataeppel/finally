"""Pydantic models for LLM structured output (PLAN.md §9).

The schema handed to the model declares enums for `side` and `action` so the
model is constrained to valid values. Parsing is deliberately a little more
forgiving than the schema: a `before` validator normalizes case and whitespace
so that "BUY" or " Buy " still validate rather than sinking the whole response.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Side = Literal["buy", "sell"]
WatchlistAction = Literal["add", "remove"]


def _normalize(value):
    return value.strip().lower() if isinstance(value, str) else value


def _normalize_ticker(value):
    return value.strip().upper() if isinstance(value, str) else value


class TradeAction(BaseModel):
    """A trade the assistant wants to execute."""

    ticker: str = Field(description="Ticker symbol, e.g. AAPL")
    side: Side = Field(description="Order side")
    quantity: float = Field(description="Number of shares; may be fractional")

    _norm_side = field_validator("side", mode="before")(_normalize)
    _norm_ticker = field_validator("ticker", mode="before")(_normalize_ticker)


class WatchlistChange(BaseModel):
    """A watchlist modification the assistant wants to make."""

    ticker: str = Field(description="Ticker symbol, e.g. PYPL")
    action: WatchlistAction = Field(description="Whether to add or remove it")

    _norm_action = field_validator("action", mode="before")(_normalize)
    _norm_ticker = field_validator("ticker", mode="before")(_normalize_ticker)


class LlmResponse(BaseModel):
    """The complete structured response returned by the assistant."""

    message: str = Field(description="Conversational response shown to the user")
    trades: list[TradeAction] = Field(
        default_factory=list, description="Trades to execute immediately"
    )
    watchlist_changes: list[WatchlistChange] = Field(
        default_factory=list, description="Watchlist additions or removals"
    )

    @field_validator("message", mode="before")
    @classmethod
    def _strip_message(cls, value):
        """Strip and reject a blank message.

        Enforced here rather than with `min_length` because the JSON schema is
        sent to the provider in strict mode, which only accepts a small subset
        of validation keywords — `minLength` is not among them.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("message must not be blank")
            return stripped
        return value
