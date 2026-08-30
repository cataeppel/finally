# backend/app/market/seeds.py
"""Starting prices, drift, volatility and sector for the simulator.

Levels approximate reality as of mid-2026 — close enough to be plausible, and nobody
trades against them. Exactness is not the goal; recognisability is. A student who sees
AAPL near $190 and NVDA near $125 believes the terminal; the third decimal place buys
nothing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class Sector(str, Enum):
    """Drives the sector factor in the correlation model (MARKET_DATA.md §4)."""

    TECH = "tech"
    CONSUMER = "consumer"
    FINANCE = "finance"
    HEALTH = "health"
    ENERGY = "energy"
    INDUSTRIAL = "industrial"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TickerSpec:
    price: float          # seed and initial anchor, USD
    drift: float          # mu, annualised
    volatility: float     # sigma, annualised (before SIM_VOL_SCALE)
    sector: Sector
    beta: float = 1.0     # loading on the market factor


T, S = TickerSpec, Sector

SEED_PRICES: dict[str, TickerSpec] = {
    # ---- the ten defaults (PLAN.md §7) ----------------------------------
    "AAPL":  T(190.00, 0.09, 0.26, S.TECH,       1.05),
    "GOOGL": T(175.00, 0.10, 0.29, S.TECH,       1.10),
    "MSFT":  T(420.00, 0.11, 0.25, S.TECH,       0.95),
    "AMZN":  T(185.00, 0.10, 0.33, S.CONSUMER,   1.20),
    "TSLA":  T(245.00, 0.05, 0.62, S.CONSUMER,   1.60),
    "NVDA":  T(125.00, 0.18, 0.55, S.TECH,       1.55),
    "META":  T(510.00, 0.12, 0.36, S.TECH,       1.25),
    "JPM":   T(215.00, 0.06, 0.24, S.FINANCE,    0.90),
    "V":     T(280.00, 0.07, 0.21, S.FINANCE,    0.85),
    "NFLX":  T(680.00, 0.10, 0.38, S.CONSUMER,   1.20),
    # ---- names PLAN.md §6 calls out as likely additions ------------------
    "PYPL":  T( 68.00, 0.04, 0.40, S.FINANCE,    1.15),
    "AMD":   T(160.00, 0.13, 0.50, S.TECH,       1.50),
    "INTC":  T( 32.00, 0.01, 0.42, S.TECH,       1.10),
    "DIS":   T( 95.00, 0.05, 0.30, S.CONSUMER,   1.05),
    "BA":    T(180.00, 0.03, 0.42, S.INDUSTRIAL, 1.30),
    "WMT":   T( 72.00, 0.08, 0.19, S.CONSUMER,   0.60),
    "KO":    T( 62.00, 0.05, 0.16, S.CONSUMER,   0.55),
    # ---- mega-cap tech ---------------------------------------------------
    "AVGO":  T(178.00, 0.14, 0.44, S.TECH,       1.35),
    "ORCL":  T(215.00, 0.11, 0.32, S.TECH,       1.05),
    "CRM":   T(265.00, 0.08, 0.35, S.TECH,       1.20),
    "ADBE":  T(390.00, 0.07, 0.34, S.TECH,       1.15),
    "QCOM":  T(165.00, 0.09, 0.38, S.TECH,       1.25),
    "MU":    T(105.00, 0.12, 0.52, S.TECH,       1.45),
    "PLTR":  T( 48.00, 0.20, 0.70, S.TECH,       1.60),
    "QQQ":   T(570.00, 0.11, 0.22, S.TECH,       1.15),
    # ---- consumer & retail ----------------------------------------------
    "COST":  T(880.00, 0.09, 0.20, S.CONSUMER,   0.75),
    "HD":    T(360.00, 0.06, 0.23, S.CONSUMER,   0.95),
    "MCD":   T(295.00, 0.05, 0.18, S.CONSUMER,   0.65),
    "NKE":   T( 78.00, 0.03, 0.31, S.CONSUMER,   1.00),
    "SBUX":  T( 92.00, 0.04, 0.29, S.CONSUMER,   0.95),
    "PG":    T(168.00, 0.05, 0.17, S.CONSUMER,   0.55),
    "F":     T( 11.50, 0.02, 0.38, S.CONSUMER,   1.20),
    "GM":    T( 48.00, 0.04, 0.36, S.CONSUMER,   1.25),
    "UBER":  T( 74.00, 0.12, 0.41, S.CONSUMER,   1.30),
    "ABNB":  T(135.00, 0.08, 0.42, S.CONSUMER,   1.25),
    "SHOP":  T( 88.00, 0.13, 0.55, S.CONSUMER,   1.50),
    "RIVN":  T( 14.00, 0.02, 0.75, S.CONSUMER,   1.60),
    "LCID":  T(  3.20, -0.05, 0.75, S.CONSUMER,  1.60),
    # ---- finance ---------------------------------------------------------
    "BRKB":  T(465.00, 0.08, 0.18, S.FINANCE,    0.80),
    "MA":    T(475.00, 0.08, 0.22, S.FINANCE,    0.90),
    "GS":    T(510.00, 0.07, 0.28, S.FINANCE,    1.15),
    "BAC":   T( 42.00, 0.05, 0.29, S.FINANCE,    1.10),
    "WFC":   T( 62.00, 0.05, 0.30, S.FINANCE,    1.10),
    "SQ":    T( 68.00, 0.09, 0.58, S.FINANCE,    1.55),
    "SOFI":  T( 10.50, 0.10, 0.66, S.FINANCE,    1.60),
    "COIN":  T(215.00, 0.15, 0.75, S.FINANCE,    1.60),
    # ---- health ----------------------------------------------------------
    "LLY":   T(820.00, 0.15, 0.30, S.HEALTH,     0.75),
    "UNH":   T(520.00, 0.07, 0.27, S.HEALTH,     0.70),
    "JNJ":   T(158.00, 0.04, 0.16, S.HEALTH,     0.60),
    "PFE":   T( 28.00, 0.02, 0.25, S.HEALTH,     0.65),
    "MRK":   T(112.00, 0.06, 0.22, S.HEALTH,     0.65),
    "ABBV":  T(190.00, 0.07, 0.23, S.HEALTH,     0.65),
    # ---- energy ----------------------------------------------------------
    "XOM":   T(118.00, 0.05, 0.28, S.ENERGY,     0.85),
    "CVX":   T(155.00, 0.04, 0.27, S.ENERGY,     0.85),
    # ---- industrial / telecom -------------------------------------------
    "T":     T( 22.00, 0.03, 0.22, S.INDUSTRIAL, 0.70),
    "VZ":    T( 41.00, 0.03, 0.21, S.INDUSTRIAL, 0.70),
    # ---- broad market ----------------------------------------------------
    "SPY":   T(640.00, 0.09, 0.16, S.OTHER,      1.00),
}

del T, S   # the aliases exist only to keep the table readable

_MIN_PRICE, _MAX_PRICE = 20.0, 500.0


def spec_for(ticker: str) -> TickerSpec:
    """Return a spec for ANY validated symbol. Never raises, never rejects.

    PLAN.md §6: the LLM manages the watchlist proactively, so rejecting an unknown
    ticker mid-conversation is a worse experience than a plausible synthetic price.
    Format validation (^[A-Z]{1,5}$) already happened at the API boundary; by the
    time we get here the symbol is acceptable by definition.

    The table always wins over the hash. AMD is in the table at $160; `_synthesise`
    would have said $104.85, and that number must never surface.
    """
    known = SEED_PRICES.get(ticker)
    return known if known is not None else _synthesise(ticker)


def _synthesise(ticker: str) -> TickerSpec:
    """Deterministic pseudo-random spec derived from the symbol itself.

    SHA-256, not the builtin hash(): Python's hash() is salted per process by
    PYTHONHASHSEED, so it would give a different price on every restart and break
    E2E reproducibility outright. This is the single most important line in the file.
    """
    h = hashlib.sha256(ticker.encode()).digest()
    price_n = int.from_bytes(h[0:8], "big")
    vol_n = int.from_bytes(h[8:12], "big")
    drift_n = int.from_bytes(h[12:16], "big")
    beta_n = int.from_bytes(h[16:20], "big")

    span = int((_MAX_PRICE - _MIN_PRICE) * 100) + 1   # cents in [20.00, 500.00]
    # Every field is rounded. Without it the top of each range lands one ULP over
    # the bound -- 0.20 + 4000/10000 == 0.6000000000000001 -- and a range assertion
    # written the obvious way fails on exactly one symbol in 4,001.
    return TickerSpec(
        price=round(_MIN_PRICE + (price_n % span) / 100.0, 2),
        drift=round(-0.05 + (drift_n % 2501) / 10_000.0, 4),    # -5% ... +20%
        volatility=round(0.20 + (vol_n % 4001) / 10_000.0, 4),  # 20% ... 60%
        sector=Sector.OTHER,
        beta=round(0.70 + (beta_n % 901) / 1_000.0, 3),         # 0.70 ... 1.60
    )
