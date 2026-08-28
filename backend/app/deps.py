# backend/app/deps.py
"""FastAPI dependency accessors shared by every router.

Kept separate from main.py so routers can depend on `market()` without importing
the app module itself, which would create a circular import (main -> routers ->
main).
"""
from __future__ import annotations

from fastapi import Request

from .market import MarketDataService


def market(request: Request) -> MarketDataService:
    return request.app.state.market
