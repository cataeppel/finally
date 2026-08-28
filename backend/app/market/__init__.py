# backend/app/market/__init__.py
"""Public surface of the market module.

Everything outside this package imports from `app.market` (this file),
`app.market.service` or `app.market.types` — and nothing else.
"""
from __future__ import annotations

import logging
import os

from .massive import MassiveSource
from .service import MarketDataService
from .simulator import SimulatedSource
from .source import MarketDataSource
from .types import PricePoint, Quote, StreamStatus, Tick, iso_z

log = logging.getLogger(__name__)

__all__ = [
    "MarketDataService", "MarketDataSource", "build_source",
    "PricePoint", "Quote", "StreamStatus", "Tick", "iso_z",
]


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("market: %s=%r is not a number — using %s", name, raw, default)
        return default


def _env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("market: %s=%r is not an integer — ignoring", name, raw)
        return None


def build_source() -> MarketDataSource:
    """Env-var driven selection — PLAN.md §5.

    Set and non-empty after stripping -> Massive. Anything else -> the simulator.
    A key of "", "  ", or the literal placeholder from .env.example must NOT send us
    down the Massive path: a student who copies .env.example and never edits it
    should get the simulator, not a wall of 401s.
    """
    key = (os.getenv("MASSIVE_API_KEY") or "").strip()
    if not key or key.lower().startswith(("your-", "your_", "<")):
        interval = _env_float("SIM_INTERVAL_MS", 500.0) / 1000.0
        log.info("market: using the simulator (interval %.0f ms)", interval * 1000)
        return SimulatedSource(
            seed=_env_int("SIM_SEED"),
            interval=interval,
            vol_scale=_env_float("SIM_VOL_SCALE", 4.0),
            half_life_hours=_env_float("SIM_HALF_LIFE_HOURS", 4.0),
        )
    log.info("market: MASSIVE_API_KEY is set — probing entitlement at startup")
    return MassiveSource(
        api_key=key,
        base_url=(os.getenv("MASSIVE_BASE_URL") or "").strip() or "https://api.massive.com",
        snapshot_interval=_env_float("MASSIVE_SNAPSHOT_INTERVAL_S", 15.0),
        grouped_interval=_env_float("MASSIVE_GROUPED_INTERVAL_S", 60.0),
    )
