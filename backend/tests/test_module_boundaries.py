"""Enforces MARKET_DATA.md §1's one-way import graph:

    service -> cache -> types
       |        ^
       +-> simulator -> seeds -> types
       +-> massive -----------> types
       +-> source ------------> types

`simulator.py` and `massive.py` never import each other and never import
`service.py`. Nothing outside `app/market/` imports anything but `service` and
`types`.
"""
from __future__ import annotations

import pathlib
import re

MARKET = pathlib.Path(__file__).parents[1] / "app" / "market"
APP = MARKET.parent


def test_sources_do_not_import_each_other():
    sim = (MARKET / "simulator.py").read_text()
    mas = (MARKET / "massive.py").read_text()
    assert "massive" not in sim
    assert "simulator" not in mas
    for f in ("simulator.py", "massive.py", "cache.py", "source.py"):
        assert "from .service" not in (MARKET / f).read_text()
        assert "import service" not in (MARKET / f).read_text()


def test_app_only_imports_the_public_surface():
    for path in APP.rglob("*.py"):
        if MARKET in path.parents or path == MARKET:
            continue
        for mod in re.findall(r"from \.+market\.(\w+)", path.read_text()):
            assert mod in {"service", "types"}, f"{path} reaches into app.market.{mod}"
        for mod in re.findall(r"from \.+market import (\w+)", path.read_text()):
            assert mod not in {
                "cache", "simulator", "massive", "source",
            }, f"{path} reaches into app.market.{mod}"
