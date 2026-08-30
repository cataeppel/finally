#!/usr/bin/env python
"""A live terminal view of the FinAlly market data backend — proof it actually runs.

    cd backend
    uv run python scripts/market_data_demo.py            # live board, 30 s
    uv run python scripts/market_data_demo.py --seconds 0        # until Ctrl-C
    uv run python scripts/market_data_demo.py --check            # fast self-test, no UI
    uv run python scripts/market_data_demo.py --event-prob 0.02  # frequent 2-5% jumps

The board is driven by the *production* path, not a shortcut: it starts a real
`MarketDataService`, subscribes the way `GET /api/stream/prices` does, and renders the
`Tick` objects that the SSE endpoint serialises. If this moves, the terminal moves.

`--check` is the opposite: no animation, a handful of assertions about determinism,
seed prices, correlation and the failure ladder, printed as PASS/FAIL lines.

See planning/MARKET_DATA.md for what any of it means.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import statistics
import sys
import time
from collections import deque
from pathlib import Path

# backend/ on the path, so `app.*` imports work when this is run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.market.service import MarketDataService
    from app.market.simulator import EVENT_PROB_PER_TICK, GbmEngine, SimulatedSource
    from app.market.source import MarketDataSource
    from app.market.types import Quote, StreamStatus, Tick, iso_z
except ModuleNotFoundError as exc:  # pragma: no cover - operator help, not logic
    sys.exit(
        f"{exc}\n\nDependencies are missing. Run this from the backend directory with uv:\n"
        "    cd backend && uv run python scripts/market_data_demo.py"
    )

#: PLAN.md §7 seed data. Duplicated from app.main.DEFAULT_WATCHLIST rather than imported
#: so the demo does not drag FastAPI in just to know ten strings.
DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]

SPARK = "▁▂▃▄▅▆▇█"


# --------------------------------------------------------------------------- output


class Style:
    """ANSI codes, or empty strings when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def green(self, s): return self._w("32", s)
    def red(self, s): return self._w("31", s)
    def yellow(self, s): return self._w("33", s)
    def dim(self, s): return self._w("2", s)
    def bold(self, s): return self._w("1", s)
    def cyan(self, s): return self._w("36", s)

    def home(self) -> str:
        """Cursor to top-left and clear everything below — one flicker-free frame."""
        return "\033[H\033[0J" if self.on else ""

    def clear(self) -> str:
        return "\033[2J\033[H" if self.on else ""


def sparkline(values: list[float], width: int) -> str:
    """Unicode block sparkline over the last `width` values, scaled to their own range."""
    tail = values[-width:]
    if len(tail) < 2:
        return ""
    lo, hi = min(tail), max(tail)
    if hi - lo < 1e-12:
        return SPARK[0] * len(tail)
    step = (hi - lo) / (len(SPARK) - 1)
    return "".join(SPARK[int((v - lo) / step)] for v in tail)


def signed(value: float, digits: int = 2) -> str:
    """PLAN.md §2: never colour alone — every figure carries an explicit + or −."""
    return f"{value:+.{digits}f}"


def arrow(direction: str) -> str:
    return {"up": "▲", "down": "▼"}.get(direction, "·")


# --------------------------------------------------------------------------- live board


class EventLog(logging.Handler):
    """Catches the simulator's `simulated event: NVDA +3.41%` lines for the footer."""

    def __init__(self, keep: int = 3) -> None:
        super().__init__(level=logging.INFO)
        self.lines: deque[str] = deque(maxlen=keep)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("simulated event"):
            self.count += 1
            self.lines.append(f"{time.strftime('%H:%M:%S')}  {record.getMessage()}")


def stream_line(batch: list[Tick]) -> str:
    """One broadcast frame as a single line, for non-tty output.

    Two glyphs per ticker, because they answer different questions: the one beside the
    price is `direction` (this tick vs. the previous one — what drives the flash in the
    UI), the one beside the percentage is the sign of the change against the session
    open. They disagree often, and that is correct.
    """
    ts = iso_z(batch[0].ts)
    body = "  ".join(
        f"{t.ticker} {t.price:.2f}{arrow(t.direction)}"
        f" {'▲' if t.change_pct >= 0 else '▼'}{signed(t.change_pct)}%"
        for t in sorted(batch, key=lambda t: t.ticker)
    )
    return f"{ts}  {body}"


def render(
    st: Style,
    svc: MarketDataService,
    latest: dict[str, Tick],
    *,
    elapsed: float,
    frames: int,
    events: EventLog,
    spark_width: int,
) -> str:
    health = svc.health
    dot = {
        StreamStatus.CONNECTED: st.green("●"),
        StreamStatus.DEGRADED: st.yellow("●"),
        StreamStatus.DISCONNECTED: st.red("●"),
    }[svc.status]

    out = [
        f"{st.bold('FinAlly')} {st.dim('· market data demo')}"
        f"    source {st.cyan(health['source'])}  {dot} {health['status']}"
        f"    poll {health['poll_interval'] * 1000:.0f}ms"
        f"    elapsed {elapsed:5.1f}s   frames {frames}",
        "",
        st.dim(
            f"  {'TICKER':<7}{'LAST':>10}{'':<3}{'OPEN':>9}{'CHANGE':>10}{'CHG%':>11}"
            f"   {'SPARKLINE':<{spark_width}} {'PTS':>5}"
        ),
    ]

    for ticker in sorted(latest):
        tick = latest[ticker]
        prices = [p.price for p in svc.history(ticker)]
        # Two references, on purpose (PLAN.md §6): `direction` compares this tick to the
        # previous one and is what flashes in the UI; `change_pct` compares to the
        # session open and is what the daily-change column shows. They differ often.
        tick_paint = {"up": st.green, "down": st.red}.get(tick.direction, st.dim)
        day_paint = st.green if tick.change >= 0 else st.red
        day_glyph = "▲" if tick.change >= 0 else "▼"
        spark = sparkline(prices, spark_width)
        # Pad INSIDE the colour helpers: ANSI escapes count toward an f-string's
        # width, so colouring first and padding second breaks every column.
        out.append(
            f"  {st.bold(f'{ticker:<7}')}"
            f"{tick_paint(f'{tick.price:>10.2f}')}"
            f"{tick_paint(f' {arrow(tick.direction)} ')}"
            f"{st.dim(f'{tick.open:>9.2f}')}"
            f"{day_paint(f'{signed(tick.change):>10}')}"
            f"{day_paint(f'{day_glyph + signed(tick.change_pct) + chr(37):>11}')}"
            f"   {day_paint(f'{spark:<{spark_width}}')} "
            f"{st.dim(f'{len(prices):>5}')}"
        )

    out += [
        "",
        st.dim(
            f"  tracked {len(health['tracked'])} · subscribers {health['subscribers']}"
            f" · ring holds up to 600 pts/ticker (GET /api/history/{{ticker}})"
            f" · jumps fired {events.count}"
        ),
    ]
    for line in events.lines:
        out.append(st.dim(f"  ⚡ {line}"))
    out.append(st.dim("  Ctrl-C to stop"))
    return "\n".join(out) + "\n"


async def live(args: argparse.Namespace, st: Style) -> int:
    events = EventLog()
    logging.getLogger("app.market.simulator").addHandler(events)
    logging.getLogger("app.market.simulator").setLevel(logging.INFO)

    source = SimulatedSource(
        seed=args.seed,
        interval=args.interval_ms / 1000.0,
        vol_scale=args.vol_scale,
        half_life_hours=args.half_life_hours,
        event_prob=args.event_prob,
    )
    svc = MarketDataService(source, broadcast_interval=args.interval_ms / 1000.0)
    await svc.start()
    svc.set_tracked(frozenset(args.tickers))

    queue = svc.subscribe()            # exactly what routes/market.py:stream_prices does
    latest: dict[str, Tick] = {}
    started, frames = time.monotonic(), 0
    added_extra = False

    if st.on:
        print(st.clear(), end="")
    try:
        while True:
            elapsed = time.monotonic() - started
            if args.seconds and elapsed >= args.seconds:
                break

            # Mid-run watchlist change: prime() must give the new ticker a price on the
            # spot, with no 500 ms hole. MARKET_DATA.md §6, addition A1.
            if args.add and not added_extra and elapsed >= (args.seconds or 30) / 2:
                added_extra = True
                svc.set_tracked(frozenset(args.tickers) | {args.add})

            try:
                batch = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                batch = []
            for tick in batch:
                latest[tick.ticker] = tick
            if batch:
                frames += 1

            if st.on:
                print(
                    st.home()
                    + render(
                        st, svc, latest,
                        elapsed=elapsed, frames=frames, events=events,
                        spark_width=args.spark,
                    ),
                    end="",
                    flush=True,
                )
            elif batch:
                # Redrawing in place needs a terminal. Piped or redirected, log one
                # line per broadcast frame instead — the same data, greppable.
                print(stream_line(batch), flush=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        svc.unsubscribe(queue)
        await svc.stop()

    summary(st, svc, latest, frames=frames, elapsed=time.monotonic() - started, events=events)
    return 0


def summary(
    st: Style,
    svc: MarketDataService,
    latest: dict[str, Tick],
    *,
    frames: int,
    elapsed: float,
    events: EventLog,
) -> None:
    print()
    print(st.bold("  session summary"))
    print(st.dim(f"  {'TICKER':<7}{'OPEN':>10}{'LAST':>10}{'LOW':>10}{'HIGH':>10}{'CHG%':>9}"))
    for ticker in sorted(latest):
        prices = [p.price for p in svc.history(ticker)] or [latest[ticker].price]
        tick = latest[ticker]
        paint = st.green if tick.change >= 0 else st.red
        print(
            f"  {ticker:<7}{tick.open:>10.2f}{tick.price:>10.2f}"
            f"{min(prices):>10.2f}{max(prices):>10.2f}"
            f"{paint(f'{signed(tick.change_pct) + chr(37):>9}')}"
        )
    moved = sum(1 for t, k in latest.items() if k.price != k.open)
    print()
    print(
        f"  {frames} broadcast frames in {elapsed:.1f}s · "
        f"{moved}/{len(latest)} tickers moved off their open · {events.count} jump events"
    )
    print(st.dim("  the stream is the same one GET /api/stream/prices serves.\n"))


# --------------------------------------------------------------------------- self-test


class _FlakySource(MarketDataSource):
    """Always fails. Used to prove the three-strikes fallback in `--check`.

    Deliberately NOT a SimulatedSource subclass: `MarketDataService._fall_back` treats
    a failing simulator as terminal (there is nothing left to fall back to), so a
    subclass would never trigger the swap this check is trying to observe.
    """

    name = "flaky"
    poll_interval = 0.01
    degraded_reason = None

    def set_tickers(self, tickers: frozenset[str]) -> None:
        self._tickers = tickers

    async def fetch(self) -> list[Quote]:
        raise RuntimeError("upstream boom")


def _log_returns(engine: GbmEngine, tickers: set[str], n: int) -> dict[str, list[float]]:
    """Read the continuous log path, not the emitted price: quotes are rounded to
    cents, and differencing rounded prices measures the rounding, not the process."""
    frozen = frozenset(tickers)
    engine.step(frozen)
    states = {t: engine._states[t] for t in tickers}          # noqa: SLF001 - demo
    prev = {t: s.log_price for t, s in states.items()}
    out: dict[str, list[float]] = {t: [] for t in tickers}
    for _ in range(n):
        engine.step(frozen)
        for t, s in states.items():
            out[t].append(s.log_price - prev[t])
            prev[t] = s.log_price
    return out


def _fingerprint(quotes) -> list[tuple]:
    """(ticker, price, session_open) per quote — the price path, without `ts`.

    Every `step()` stamps the real wall clock, so two identically seeded engines never
    share a timestamp. Determinism is a claim about the prices, not about the clock.
    """
    return sorted((q.ticker, q.price, q.session_open) for q in quotes)


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va and vb else 0.0


async def check(st: Style) -> int:
    from app.market.seeds import SEED_PRICES, _synthesise, spec_for   # noqa: PLC0415

    # The service logs the fallback it is asked to perform in check 8; that is
    # expected here, so keep it out of the report.
    logging.getLogger("app.market").setLevel(logging.CRITICAL)

    results: list[tuple[bool, str, str]] = []

    def record(ok: bool, name: str, detail: str) -> None:
        results.append((ok, name, detail))

    # 1. same seed, same sequence — the E2E reproducibility guarantee
    a, b = GbmEngine(seed=42), GbmEngine(seed=42)
    ts = frozenset({"AAPL", "MSFT", "JPM"})
    seq_a = [_fingerprint(a.step(ts)) for _ in range(500)]
    seq_b = [_fingerprint(b.step(ts)) for _ in range(500)]
    record(seq_a == seq_b, "determinism", "500 ticks × 2 engines at seed 42 are identical")

    # 2. different seeds must differ, or (1) could pass by emitting constants
    c, d = GbmEngine(seed=1), GbmEngine(seed=2)
    one = frozenset({"AAPL"})
    record(
        [_fingerprint(c.step(one)) for _ in range(100)]
        != [_fingerprint(d.step(one)) for _ in range(100)],
        "seed sensitivity", "seed 1 and seed 2 produce different paths",
    )

    # 3. the table beats the hash, and unknown symbols still get a plausible price
    snow = spec_for("SNOW")
    record(
        spec_for("AMD").price == 160.00 and snow.price == 348.97,
        "seed prices", f"AMD=$160.00 from the table, SNOW=${snow.price} from sha256",
    )

    # 4. no symbol matching ^[A-Z]{1,5}$ is ever rejected, and all land in range
    sample = [f"{x}{y}{z}" for x in "AZ" for y in "AMZ" for z in "AKZ"] + ["A", "ABCDE", "ZZZZZ"]
    in_range = all(
        20.0 <= _synthesise(s).price <= 500.0 and 0.20 <= _synthesise(s).volatility <= 0.60
        for s in sample
    )
    record(in_range, "unknown tickers", f"{len(sample)} synthetic symbols priced, all in range")

    # 5. prices actually move — the whole point of the simulator being the default
    eng = GbmEngine(seed=3)
    prices = {q.price for _ in range(200) for q in eng.step(one)}
    record(len(prices) > 20, "price motion", f"{len(prices)} distinct AAPL prices over 200 ticks")

    # 6. correlation lands on the closed form (jumps and reversion off — they swamp it)
    eng = GbmEngine(seed=11, vol_scale=1.0, half_life_hours=0.0, event_prob=0.0)
    paths = _log_returns(eng, {"AAPL", "MSFT", "KO"}, 40_000)
    tech = _corr(paths["AAPL"], paths["MSFT"])
    cross = _corr(paths["AAPL"], paths["KO"])
    record(
        abs(tech - 0.377) < 0.04 and abs(cross - 0.138) < 0.04,
        "correlation", f"AAPL/MSFT {tech:.3f} (≈0.377), AAPL/KO {cross:.3f} (≈0.138)",
    )

    # 7. the cache derives change against the OPEN and direction against the PREV tick
    svc = MarketDataService(SimulatedSource(seed=42, interval=0.01), broadcast_interval=0.01)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    primed = svc.price("AAPL") is not None          # prime(): a price with no poll yet
    await asyncio.sleep(0.4)
    tick = svc.snapshot()["AAPL"]
    derived = (
        abs(tick.change - (tick.price - tick.open)) < 1e-9
        and tick.direction in {"up", "down", "flat"}
        and len(svc.history("AAPL")) > 1
    )
    await svc.stop()
    record(primed and derived, "cache derivation",
           "prime() fills a new ticker instantly; change vs open, direction vs prev tick")

    # 8. three consecutive failures swap in the simulator and turn the dot yellow.
    # Takes a few seconds on purpose: failures 1 and 2 back off exponentially with
    # jitter (1-3 s, then 2-6 s) before the third one trips the ladder.
    print(st.dim("  running the failure ladder (a few seconds of backoff)..."), flush=True)
    svc = MarketDataService(_FlakySource(), broadcast_interval=0.01)
    await svc.start()
    svc.set_tracked(frozenset({"AAPL"}))
    began, deadline = time.monotonic(), time.monotonic() + 15.0
    while svc.health["source"] != "simulator" and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    took = time.monotonic() - began
    fell_back = svc.health["source"] == "simulator" and svc.status is StreamStatus.DEGRADED
    # The replacement simulator polls at its own 500 ms cadence, so give it one tick.
    await asyncio.sleep(0.8)
    resumed = svc.price("AAPL") is not None      # the stream must survive the swap
    await svc.stop()
    record(fell_back and resumed, "failure ladder",
           f"3 failed polls → simulator in {took:.1f}s, degraded, prices resumed")

    print()
    for ok, name, detail in results:
        mark = st.green("PASS") if ok else st.red("FAIL")
        print(f"  [{mark}] {st.bold(f'{name:<18}')} {st.dim(detail)}")
    failed = sum(1 for ok, _, _ in results if not ok)
    print()
    print(f"  {len(results) - failed}/{len(results)} checks passed "
          f"· {len(SEED_PRICES)} tickers in the seed table\n")
    return 1 if failed else 0


# --------------------------------------------------------------------------- entrypoint


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live view of the FinAlly market simulator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--seconds", type=float, default=30.0,
                   help="how long to run; 0 means until Ctrl-C")
    p.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS,
                   help="symbols to track (the default watchlist)")
    p.add_argument("--add", metavar="TICKER",
                   help="add this ticker halfway through, to show prime() filling it instantly")
    p.add_argument("--seed", type=int, default=42,
                   help="RNG seed; the same seed always replays the same prices")
    p.add_argument("--interval-ms", type=float, default=500.0, help="tick cadence")
    p.add_argument("--vol-scale", type=float, default=4.0,
                   help="volatility multiplier; 1.0 is statistically honest, 4.0 is watchable")
    p.add_argument("--half-life-hours", type=float, default=4.0,
                   help="mean-reversion half-life; 0 disables reversion")
    p.add_argument("--event-prob", type=float, default=EVENT_PROB_PER_TICK,
                   help="per-ticker per-tick chance of a 2-5%% jump; try 0.02 for drama")
    p.add_argument("--spark", type=int, default=40, help="sparkline width in characters")
    p.add_argument("--check", action="store_true",
                   help="run the fast self-test instead of the live board")
    p.add_argument("--no-color", action="store_true", help="plain output, no ANSI")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    st = Style(enabled=not args.no_color and sys.stdout.isatty())
    if args.check:
        return asyncio.run(check(st))
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(live(args, st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
