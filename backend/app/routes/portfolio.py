"""Portfolio API endpoints (PLAN.md §8)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import get_portfolio_history

from .trading import TradeError, execute_trade_order, value_portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    ticker: str
    side: str  # "buy" or "sell"
    quantity: float


@router.get("")
async def get_portfolio(request: Request):
    """Current positions, cash balance, total value, unrealized P&L."""
    return await value_portfolio(request.app.state.price_cache)


@router.post("/trade")
async def execute_trade(trade: TradeRequest, request: Request):
    """Execute a market order. Instant fill at the current cached price."""
    try:
        return await execute_trade_order(
            trade.ticker, trade.side, trade.quantity, request.app.state.price_cache
        )
    except TradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/history")
async def portfolio_history():
    """Portfolio value snapshots over time (oldest first), for the P&L chart."""
    return {"snapshots": await get_portfolio_history()}
