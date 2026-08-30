"""MARKET_DATA.md §10.

Every statistical test runs with BOTH `half_life_hours=0.0` AND `event_prob=0.0`.
Both are required: kappa=0 makes the analytic result exact, and disabling jumps
isolates the diffusion -- with jumps on, sample variance is ~130x the closed form
and measured correlation collapses (§7.2.1). A test written without both flags
fails against correct code.
"""
from __future__ import annotations

import dataclasses
import math
import statistics

import pytest

from app.market.seeds import spec_for
from app.market.simulator import TRADING_YEAR_SECONDS, GbmEngine


def _collect_log_returns(eng: GbmEngine, tickers: set[str], n: int) -> dict[str, list[float]]:
    frozen = frozenset(tickers)
    eng.step(frozen)  # mint state
    prev = {t: eng._states[t].log_price for t in tickers}
    rets: dict[str, list[float]] = {t: [] for t in tickers}
    for _ in range(n):
        eng.step(frozen)
        for t in tickers:
            lp = eng._states[t].log_price
            rets[t].append(lp - prev[t])
            prev[t] = lp
    return rets


def _correlation(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
    sa = statistics.pstdev(a)
    sb = statistics.pstdev(b)
    return cov / (sa * sb)


# ---- statistical --------------------------------------------------------


def test_gbm_log_returns_match_the_analytic_distribution():
    """With kappa=0 and no jumps, log(S_t+1/S_t) ~ N((mu - sigma^2/2)*dt, sigma^2*dt)."""
    eng = GbmEngine(seed=7, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    tickers = frozenset({"AAPL"})
    eng.step(tickers)

    state = eng._states["AAPL"]
    rets, prev = [], state.log_price
    for _ in range(50_000):
        eng.step(tickers)
        rets.append(state.log_price - prev)
        prev = state.log_price

    spec = spec_for("AAPL")
    dt = 0.5 / TRADING_YEAR_SECONDS
    exp_mean = (spec.drift - 0.5 * spec.volatility**2) * dt
    exp_var = spec.volatility**2 * dt

    mean, var = statistics.fmean(rets), statistics.pvariance(rets)
    assert abs(mean - exp_mean) < 4 * math.sqrt(exp_var / len(rets))
    assert var == pytest.approx(exp_var, rel=0.10)


def test_correlation_matches_the_closed_form():
    """rho_ij = (w_m*bh_i*bh_j + w_s[same sector]) / (n_i * n_j) -- §7.3."""
    eng = GbmEngine(seed=11, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    paths = _collect_log_returns(eng, {"AAPL", "MSFT", "JPM", "NVDA", "KO"}, n=40_000)
    for a, b, expected in [
        ("AAPL", "MSFT", 0.377),      # tech / tech
        ("NVDA", "MSFT", 0.426),
        ("AAPL", "NVDA", 0.447),
        ("AAPL", "JPM", 0.212),       # tech / finance
        ("AAPL", "KO", 0.138),        # tech / consumer
    ]:
        assert _correlation(paths[a], paths[b]) == pytest.approx(expected, abs=0.05)


def test_no_drift_no_vol_is_a_flat_line():
    eng = GbmEngine(seed=1, event_prob=0.0)
    tickers = frozenset({"FLAT"})
    eng.ensure(tickers)
    st = eng._states["FLAT"]
    st.spec = dataclasses.replace(st.spec, price=100.0, drift=0.0, volatility=0.0)
    start_price = st.log_price
    for _ in range(2000):
        eng.step(tickers)
    assert st.log_price == pytest.approx(start_price, abs=1e-12)


def test_z_has_unit_variance_for_every_beta():
    eng = GbmEngine(seed=3, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    tickers = {"AAPL", "NVDA", "KO"}   # beta 1.05, 1.55, 0.55
    paths = _collect_log_returns(eng, tickers, n=40_000)
    dt = 0.5 / TRADING_YEAR_SECONDS
    for t in tickers:
        sigma = spec_for(t).volatility
        realised_sd = statistics.pstdev(paths[t])
        expected_sd = sigma * math.sqrt(dt)
        assert realised_sd / expected_sd == pytest.approx(1.0, abs=0.03)


def test_a_tickers_volatility_does_not_depend_on_the_watchlist():
    """The C2 guard: MEAN_BETA is a module constant, not the current watchlist's mean."""
    eng_a = GbmEngine(seed=5, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    paths_a = _collect_log_returns(eng_a, {"NVDA", "TSLA", "PLTR"}, n=30_000)

    eng_b = GbmEngine(seed=5, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    paths_b = _collect_log_returns(eng_b, {"NVDA", "KO", "JNJ"}, n=30_000)

    sd_a = statistics.pstdev(paths_a["NVDA"])
    sd_b = statistics.pstdev(paths_b["NVDA"])
    assert sd_a / sd_b == pytest.approx(1.0, abs=0.05)


def test_mean_reversion_bounds_a_long_run():
    """8 simulated hours, jumps off: every ticker stays within +-25% of its seed."""
    ticks_per_hour = int(3600 / 0.5)
    for seed in range(3):
        eng = GbmEngine(seed=seed, event_prob=0.0)
        tickers = frozenset({"AAPL", "TSLA", "NVDA", "KO"})
        eng.step(tickers)
        for _ in range(8 * ticks_per_hour):
            eng.step(tickers)
        for t in tickers:
            price = math.exp(eng._states[t].log_price)
            seed_price = spec_for(t).price
            assert abs(price / seed_price - 1.0) < 0.25, (seed, t, price, seed_price)


# ---- events ---------------------------------------------------------------


def test_events_fire_at_the_expected_rate():
    eng = GbmEngine(seed=9, event_prob=0.5)
    tickers = frozenset({"AAPL"})
    prices = []
    for _ in range(200):
        eng.step(tickers)
        prices.append(math.exp(eng._states["AAPL"].log_price))
    # with event_prob=0.5, ~100 of 200 ticks jump; consecutive prices should show
    # large relative moves well beyond ordinary diffusion (a jump is 2-5%, diffusion
    # at these settings is a fraction of a percent).
    big_moves = sum(
        1 for a, b in zip(prices, prices[1:]) if abs(math.log(b / a)) > 0.01
    )
    assert big_moves > 60   # expected ~100; generous margin against RNG variance


def test_event_moves_the_anchor_halfway():
    eng = GbmEngine(seed=1, event_prob=1.0, half_life_hours=0.0)
    tickers = frozenset({"AAPL"})
    eng.step(tickers)
    st = eng._states["AAPL"]
    anchor_before, price_before = st.log_anchor, st.log_price
    eng.step(tickers)
    price_delta = st.log_price - price_before
    anchor_delta = st.log_anchor - anchor_before
    # the drift term on the anchor is tiny; the jump dominates, so the ratio should
    # sit close to 0.5 (EVENT_ANCHOR_SHARE), with a little slack for the diffusion term.
    assert anchor_delta / price_delta == pytest.approx(0.5, abs=0.05)


def test_prices_are_rounded_to_cents_but_state_is_not():
    """Emitted prices have <=2 decimals, but internal log_price retains full float
    precision -- rounding the state itself would inject a bias on every tick
    (MARKET_DATA.md §4)."""
    eng = GbmEngine(seed=2)
    tickers = frozenset({"LCID"})   # a cheap stock, $3.20 seed
    quotes = []
    sub_cent_moves = 0
    eng.ensure(tickers)
    prev_raw = math.exp(eng._states["LCID"].log_price)
    for _ in range(200):
        quotes.extend(eng.step(tickers))
        raw = math.exp(eng._states["LCID"].log_price)
        if raw != prev_raw and abs(raw - prev_raw) < 0.005:
            sub_cent_moves += 1
        prev_raw = raw
    for q in quotes:
        assert q.price == round(q.price, 2)
    # the internal path takes many sub-cent steps that a 2-decimal quantisation
    # would otherwise erase -- proof the state itself is never rounded.
    assert sub_cent_moves > 0


def test_new_ticker_added_mid_run_starts_at_its_seed_price():
    eng = GbmEngine(seed=4)
    eng.step(frozenset({"AAPL"}))
    quotes = eng.step(frozenset({"AAPL", "PYPL"}))
    pypl = next(q for q in quotes if q.ticker == "PYPL")
    assert pypl.session_open == spec_for("PYPL").price


# ---- determinism ------------------------------------------------------------


def _fingerprint(quotes) -> list[tuple]:
    """(ticker, price, session_open) per quote, sorted by ticker.

    Deliberately excludes `ts`: each `step()` stamps the real wall clock, so two
    sequential calls -- even from engines seeded identically -- never share a
    timestamp. Determinism is a claim about the PRICE PATH, not about when the
    test happened to run.
    """
    return sorted((q.ticker, q.price, q.session_open) for q in quotes)


def test_same_seed_same_sequence():
    a, b = GbmEngine(seed=42), GbmEngine(seed=42)
    ts = frozenset({"AAPL", "MSFT", "JPM"})
    seq_a = [_fingerprint(a.step(ts)) for _ in range(500)]
    seq_b = [_fingerprint(b.step(ts)) for _ in range(500)]
    assert seq_a == seq_b


def test_different_seed_different_sequence():
    """The complement, so the test above cannot pass by producing constants."""
    a, b = GbmEngine(seed=1), GbmEngine(seed=2)
    ts = frozenset({"AAPL"})
    seq_a = [_fingerprint(a.step(ts)) for _ in range(100)]
    seq_b = [_fingerprint(b.step(ts)) for _ in range(100)]
    assert seq_a != seq_b


def test_step_with_an_empty_set_draws_no_rng():
    a, b = GbmEngine(seed=1), GbmEngine(seed=1)
    for _ in range(100):
        a.step(frozenset())
    out_a = _fingerprint(a.step(frozenset({"AAPL"})))
    out_b = _fingerprint(b.step(frozenset({"AAPL"})))
    assert out_a == out_b


def test_forget_releases_state():
    eng = GbmEngine(seed=1)
    eng.ensure(frozenset({"AAPL"}))
    eng._states["AAPL"].log_price += 1.0   # perturb away from the seed
    eng.forget(frozenset())
    assert "AAPL" not in eng._states
    eng.ensure(frozenset({"AAPL"}))
    assert eng._states["AAPL"].log_price == math.log(spec_for("AAPL").price)


def test_prime_consumes_no_randomness():
    a, b = GbmEngine(seed=1), GbmEngine(seed=1)
    ts = frozenset({"AAPL", "MSFT"})
    a.step(ts)
    a.seed_quotes(ts)     # extra call, must not touch the RNG
    b.step(ts)
    assert _fingerprint(a.step(ts)) == _fingerprint(b.step(ts))
