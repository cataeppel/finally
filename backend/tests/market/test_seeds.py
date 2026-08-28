from __future__ import annotations

import itertools
import os
import string
import subprocess
import sys

from app.market.seeds import SEED_PRICES, Sector, _synthesise, spec_for

_MIN_PRICE, _MAX_PRICE = 20.0, 500.0


def test_known_tickers_use_the_table():
    assert spec_for("AAPL").price == 190.00
    # AMD is in the table at 160.00 -- the hash alone would say 104.85 (MARKET_DATA_DESIGN.md
    # §16.2 C1), so the table must win.
    assert spec_for("AMD").price == 160.00
    assert SEED_PRICES["AMD"] is spec_for("AMD")


def test_table_wins_over_the_hash():
    known = spec_for("PYPL")
    hashed = _synthesise("PYPL")
    assert known.price != hashed.price
    assert known.price == 68.00


def test_unknown_ticker_lands_in_range():
    """Sweep _synthesise directly -- spec_for trips over table entries outside the
    $20-$500 band (LLY $820, SPY $640, NFLX $680, ...)."""
    letters = string.ascii_uppercase
    count = 0
    for combo in itertools.product(letters, repeat=3):
        ticker = "".join(combo)
        if ticker in SEED_PRICES:
            continue
        spec = _synthesise(ticker)
        assert _MIN_PRICE <= spec.price <= _MAX_PRICE, ticker
        assert 0.20 <= spec.volatility <= 0.60, ticker
        assert -0.05 <= spec.drift <= 0.20, ticker
        assert 0.70 <= spec.beta <= 1.60, ticker
        assert spec.sector is Sector.OTHER
        count += 1
    assert count > 17000


def test_synthesised_values_are_rounded():
    """The top of each range must land exactly on the bound, not one ULP over it
    (0.20 + 4000/10000 == 0.6000000000000001 without rounding)."""
    for combo in itertools.product(string.ascii_uppercase, repeat=2):
        ticker = "".join(combo)
        spec = _synthesise(ticker)
        assert round(spec.price, 2) == spec.price
        assert round(spec.drift, 4) == spec.drift
        assert round(spec.volatility, 4) == spec.volatility
        assert round(spec.beta, 3) == spec.beta


def test_unknown_ticker_is_never_rejected():
    for ticker in ("A", "ZZZZZ", "SNOW", "QQ", "ABCDE"):
        spec = spec_for(ticker)
        assert spec.price > 0


def test_snow_reference_value():
    """MARKET_DATA_DESIGN.md §16.2 C1: PYPL is in the table so 178.51 is the wrong
    determinism literal. SNOW is genuinely absent from the table."""
    assert "SNOW" not in SEED_PRICES
    assert spec_for("SNOW").price == 348.97


def test_seed_price_is_stable_across_processes():
    """Catches someone swapping hashlib.sha256 for the builtin hash(): hash() is
    salted per-process by PYTHONHASHSEED and would break E2E reproducibility."""
    code = "from app.market.seeds import spec_for; print(spec_for('SNOW').price)"
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    for hashseed in ("0", "1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=backend_dir,
            env={**os.environ, "PYTHONHASHSEED": hashseed},
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "348.97"
