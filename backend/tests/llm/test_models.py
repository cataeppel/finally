"""Tests for LLM structured output models."""

import pytest
from pydantic import ValidationError

from app.llm.models import LlmResponse, TradeAction, WatchlistChange


class TestLlmResponse:
    def test_message_only(self):
        resp = LlmResponse(message="Hello")
        assert resp.message == "Hello"
        assert resp.trades == []
        assert resp.watchlist_changes == []

    def test_with_trades(self):
        resp = LlmResponse(
            message="Buying AAPL",
            trades=[TradeAction(ticker="AAPL", side="buy", quantity=10)],
        )
        assert len(resp.trades) == 1
        assert resp.trades[0].ticker == "AAPL"

    def test_with_watchlist_changes(self):
        resp = LlmResponse(
            message="Adding PYPL",
            watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
        )
        assert len(resp.watchlist_changes) == 1

    def test_parse_from_json(self):
        raw = '{"message": "Done", "trades": [{"ticker": "MSFT", "side": "sell", "quantity": 5}], "watchlist_changes": []}'
        resp = LlmResponse.model_validate_json(raw)
        assert resp.message == "Done"
        assert resp.trades[0].ticker == "MSFT"
        assert resp.trades[0].side == "sell"
        assert resp.trades[0].quantity == 5

    def test_parse_minimal_json(self):
        raw = '{"message": "Hi"}'
        resp = LlmResponse.model_validate_json(raw)
        assert resp.message == "Hi"
        assert resp.trades == []
        assert resp.watchlist_changes == []


class TestNormalization:
    def test_side_case_is_normalized(self):
        assert TradeAction(ticker="AAPL", side="BUY", quantity=1).side == "buy"
        assert TradeAction(ticker="AAPL", side=" Sell ", quantity=1).side == "sell"

    def test_ticker_case_is_normalized(self):
        assert TradeAction(ticker=" aapl ", side="buy", quantity=1).ticker == "AAPL"

    def test_watchlist_action_is_normalized(self):
        assert WatchlistChange(ticker="pypl", action="ADD").action == "add"
        assert WatchlistChange(ticker="pypl", action="Remove").action == "remove"

    def test_message_is_stripped(self):
        assert LlmResponse(message="  Hello  ").message == "Hello"


class TestStrictness:
    @pytest.mark.parametrize("side", ["hold", "short", "", "buy!"])
    def test_invalid_side_rejected(self, side):
        with pytest.raises(ValidationError):
            TradeAction(ticker="AAPL", side=side, quantity=1)

    @pytest.mark.parametrize("action", ["delete", "watch", ""])
    def test_invalid_watchlist_action_rejected(self, action):
        with pytest.raises(ValidationError):
            WatchlistChange(ticker="AAPL", action=action)

    @pytest.mark.parametrize("message", ["", "   "])
    def test_blank_message_rejected(self, message):
        with pytest.raises(ValidationError):
            LlmResponse(message=message)


class TestJsonSchema:
    """The schema handed to the model must constrain side/action to enums."""

    def test_schema_declares_enums(self):
        schema = LlmResponse.model_json_schema()
        defs = schema["$defs"]
        assert defs["TradeAction"]["properties"]["side"]["enum"] == ["buy", "sell"]
        assert defs["WatchlistChange"]["properties"]["action"]["enum"] == ["add", "remove"]

    def test_schema_top_level_fields(self):
        schema = LlmResponse.model_json_schema()
        assert set(schema["properties"]) == {"message", "trades", "watchlist_changes"}
        assert schema["required"] == ["message"]
