#!/usr/bin/env python
"""Which Massive tier is this key on? Run: uv run python scripts/check_massive.py"""
import asyncio
import os
import sys

import httpx

BASE = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")


async def main() -> int:
    key = (os.getenv("MASSIVE_API_KEY") or "").strip()
    if not key:
        print("MASSIVE_API_KEY is not set — FinAlly will use the simulator. That is fine.")
        return 0
    async with httpx.AsyncClient(
        base_url=BASE, headers={"Authorization": f"Bearer {key}"}, timeout=10.0
    ) as c:
        prev = await c.get("/v2/aggs/ticker/AAPL/prev")
        if prev.status_code == 401:
            print("✗ 401 — the key is not valid. FinAlly will fall back to the simulator.")
            return 1
        print(f"✓ key accepted (AAPL prev close: {prev.json()['results'][0]['c']})")

        snap = await c.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers", params={"tickers": "AAPL,MSFT"}
        )
        if snap.status_code == 200:
            print("✓ SNAPSHOT mode — Starter or above, 15 s polling, live-ish prices")
        elif snap.status_code == 403:
            print("● GROUPED mode — free tier: END-OF-DAY prices, 60 s polling.")
            print("  The connection dot will be YELLOW and prices will not move.")
            print("  Unset MASSIVE_API_KEY to use the simulator instead — it moves.")
        else:
            print(f"? unexpected {snap.status_code}: {snap.text[:200]}")

        st = (await c.get("/v1/marketstatus/now")).json()
        print(f"  market is {st.get('market')} (server time {st.get('serverTime')})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
