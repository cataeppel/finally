from __future__ import annotations

import asyncio
import json
import pathlib
import time

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(*parts: str) -> dict:
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


async def wait_for(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    """Poll `predicate()` until it is truthy or `timeout` seconds have elapsed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    assert predicate(), f"condition not met within {timeout}s"
