"""Fixtures for route tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_watchlist_tickers, init_db, set_db_path
from app.market import MarketDataSource, PriceCache


@pytest.fixture
async def test_db(tmp_path):
    """Create a temporary test database."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    await init_db()
    yield db_path
    # Reset to default after test
    set_db_path(str(tmp_path / "unused.db"))


@pytest.fixture
def price_cache():
    """A PriceCache with some test prices."""
    cache = PriceCache()
    cache.update("AAPL", 190.50)
    cache.update("GOOGL", 175.25)
    cache.update("MSFT", 420.00)
    return cache


class FakeMarketSource(MarketDataSource):
    """In-memory MarketDataSource that seeds a price on add, like the simulator."""

    SEED_PRICE = 100.0

    def __init__(self, cache: PriceCache, tickers: list[str]):
        self._cache = cache
        self._tickers = list(tickers)

    async def start(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)
        if self._cache.get_price(ticker) is None:
            self._cache.update(ticker, self.SEED_PRICE)

    async def remove_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


@pytest.fixture
async def market_source(test_db, price_cache):
    """A fake source already tracking the seeded watchlist, as after startup."""
    return FakeMarketSource(price_cache, await get_watchlist_tickers())


@pytest.fixture
async def client(test_db, price_cache, market_source):
    """Async HTTP client wired to the FastAPI app, bypassing lifespan."""
    from fastapi import FastAPI

    from app.routes.chat import router as chat_router
    from app.routes.portfolio import router as portfolio_router
    from app.routes.watchlist import router as watchlist_router

    # Build a test app without the full lifespan (no real market data source)
    test_app = FastAPI()
    test_app.include_router(portfolio_router)
    test_app.include_router(watchlist_router)
    test_app.include_router(chat_router)

    @test_app.get("/api/health")
    async def health():
        return {"status": "ok"}

    test_app.state.price_cache = price_cache
    test_app.state.market_source = market_source

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
