"""Tests for watchlist endpoints."""



class TestGetWatchlist:
    async def test_default_watchlist(self, client):
        resp = await client.get("/api/watchlist")
        assert resp.status_code == 200
        data = resp.json()
        tickers = [item["ticker"] for item in data["watchlist"]]
        assert "AAPL" in tickers
        assert "GOOGL" in tickers
        assert len(tickers) == 10

    async def test_watchlist_includes_prices(self, client):
        resp = await client.get("/api/watchlist")
        data = resp.json()
        aapl = next(item for item in data["watchlist"] if item["ticker"] == "AAPL")
        assert aapl["price"] == 190.50


class TestAddTicker:
    async def test_add_new_ticker(self, client, price_cache):
        price_cache.update("PYPL", 65.00)
        resp = await client.post("/api/watchlist", json={"ticker": "PYPL"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "PYPL"
        assert "added_at" in data

    async def test_add_duplicate_ticker(self, client):
        resp = await client.post("/api/watchlist", json={"ticker": "AAPL"})
        assert resp.status_code == 409


class TestRemoveTicker:
    async def test_remove_existing_ticker(self, client):
        resp = await client.delete("/api/watchlist/AAPL")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

    async def test_remove_nonexistent_ticker(self, client):
        resp = await client.delete("/api/watchlist/DOESNOTEXIST")
        assert resp.status_code == 404


class TestAddTickerValidation:
    async def test_ticker_is_normalized_to_uppercase(self, client, price_cache):
        price_cache.update("PYPL", 65.00)
        resp = await client.post("/api/watchlist", json={"ticker": " pypl "})
        assert resp.status_code == 200
        assert resp.json()["ticker"] == "PYPL"

        listed = await client.get("/api/watchlist")
        assert "PYPL" in [item["ticker"] for item in listed.json()["watchlist"]]

    async def test_duplicate_detected_case_insensitively(self, client):
        resp = await client.post("/api/watchlist", json={"ticker": "aapl"})
        assert resp.status_code == 409

    async def test_rejects_invalid_ticker(self, client):
        for bad in ["", "   ", "123", "AA PL", "WAYTOOLONGTICKER"]:
            resp = await client.post("/api/watchlist", json={"ticker": bad})
            assert resp.status_code == 400, bad


class TestRemoveTickerKeepsHeldPositions:
    async def test_held_ticker_keeps_its_price(self, client, price_cache):
        await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 1},
        )
        resp = await client.delete("/api/watchlist/AAPL")
        assert resp.status_code == 200

        # Still priced, so the position can be valued and sold.
        assert price_cache.get_price("AAPL") == 190.50
        sell = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "sell", "quantity": 1},
        )
        assert sell.status_code == 200

    async def test_unheld_ticker_stops_streaming(self, client, price_cache):
        assert await client.delete("/api/watchlist/AAPL")
        assert price_cache.get_price("AAPL") is None

    async def test_remove_is_case_insensitive(self, client):
        resp = await client.delete("/api/watchlist/googl")
        assert resp.status_code == 200
