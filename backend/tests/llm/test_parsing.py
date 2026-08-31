"""Tests for structured-output parsing and its graceful-degradation paths."""

import pytest

from app.llm.parsing import LlmParseError, parse_llm_response


class TestStrictParsing:
    def test_plain_json(self):
        resp = parse_llm_response('{"message": "Done", "trades": [], "watchlist_changes": []}')
        assert resp.message == "Done"
        assert resp.trades == []

    def test_minimal_json(self):
        resp = parse_llm_response('{"message": "Hi"}')
        assert resp.message == "Hi"

    def test_full_payload(self):
        raw = (
            '{"message": "Buying", '
            '"trades": [{"ticker": "aapl", "side": "BUY", "quantity": 3}], '
            '"watchlist_changes": [{"ticker": "pypl", "action": "Add"}]}'
        )
        resp = parse_llm_response(raw)
        assert resp.trades[0].ticker == "AAPL"
        assert resp.trades[0].side == "buy"
        assert resp.watchlist_changes[0].ticker == "PYPL"
        assert resp.watchlist_changes[0].action == "add"


class TestFencedAndWrappedJson:
    def test_markdown_fence(self):
        resp = parse_llm_response('```json\n{"message": "Fenced"}\n```')
        assert resp.message == "Fenced"

    def test_bare_fence(self):
        resp = parse_llm_response('```\n{"message": "Fenced"}\n```')
        assert resp.message == "Fenced"

    def test_prose_preamble_around_json(self):
        raw = 'Sure, here you go:\n{"message": "Extracted", "trades": []}\nHope that helps.'
        resp = parse_llm_response(raw)
        assert resp.message == "Extracted"

    def test_braces_inside_strings_do_not_confuse_extraction(self):
        raw = 'Note:\n{"message": "Use {curly} braces", "trades": []}\ndone'
        resp = parse_llm_response(raw)
        assert resp.message == "Use {curly} braces"


class TestSalvage:
    def test_bad_trade_is_dropped_message_survives(self):
        raw = (
            '{"message": "Partly ok", '
            '"trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}, '
            '{"ticker": "MSFT", "side": "buy", "quantity": 2}]}'
        )
        resp = parse_llm_response(raw)
        assert resp.message == "Partly ok"
        assert len(resp.trades) == 1
        assert resp.trades[0].ticker == "MSFT"

    def test_bad_watchlist_change_is_dropped(self):
        raw = (
            '{"message": "Partly ok", '
            '"watchlist_changes": [{"ticker": "AAPL", "action": "sell"}, '
            '{"ticker": "PYPL", "action": "add"}]}'
        )
        resp = parse_llm_response(raw)
        assert len(resp.watchlist_changes) == 1
        assert resp.watchlist_changes[0].ticker == "PYPL"

    def test_null_action_lists_are_tolerated(self):
        resp = parse_llm_response('{"message": "Nulls", "trades": null, "watchlist_changes": null}')
        assert resp.trades == []
        assert resp.watchlist_changes == []

    def test_non_dict_trade_entry_dropped(self):
        resp = parse_llm_response('{"message": "Ok", "trades": ["buy AAPL"]}')
        assert resp.message == "Ok"
        assert resp.trades == []


class TestProseFallback:
    def test_plain_prose_becomes_the_message(self):
        resp = parse_llm_response("Your portfolio looks well diversified.")
        assert resp.message == "Your portfolio looks well diversified."
        assert resp.trades == []

    def test_prose_never_executes_actions(self):
        resp = parse_llm_response("I will buy 10 AAPL for you.")
        assert resp.trades == []


class TestUnrecoverable:
    @pytest.mark.parametrize("content", [None, "", "   ", "\n\t "])
    def test_empty_raises(self, content):
        with pytest.raises(LlmParseError):
            parse_llm_response(content)

    def test_json_without_message_raises(self):
        with pytest.raises(LlmParseError):
            parse_llm_response('{"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]}')

    def test_blank_message_raises(self):
        with pytest.raises(LlmParseError):
            parse_llm_response('{"message": "   ", "trades": []}')
