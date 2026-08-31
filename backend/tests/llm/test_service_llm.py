"""Tests for the real-LLM code path: prompt construction, error handling, no live calls.

The network is never touched — `app.llm.service.completion` is monkeypatched in
every test here, and a guard fixture fails the test if the real one is reached.
"""

import pytest

from app.db import init_db, insert_chat_message, set_db_path, upsert_position
from app.llm import service
from app.market import PriceCache


@pytest.fixture
async def test_db(tmp_path):
    set_db_path(str(tmp_path / "test.db"))
    await init_db()
    yield
    set_db_path(str(tmp_path / "unused.db"))


@pytest.fixture
def price_cache():
    cache = PriceCache()
    cache.update("AAPL", 190.50)
    cache.update("MSFT", 420.00)
    return cache


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    """Force the real-LLM branch with a dummy key; never a real one."""
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any un-stubbed call to litellm fails loudly rather than hitting the network."""

    def explode(*args, **kwargs):
        raise AssertionError("the test suite must never make a live LLM call")

    monkeypatch.setattr(service, "completion", explode)


def _stub_completion(monkeypatch, content, capture=None):
    """Replace service.completion with one returning `content`."""

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    def fake_completion(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return _Response(content)

    monkeypatch.setattr(service, "completion", fake_completion)


class TestCerebrasCallShape:
    async def test_uses_cerebras_provider_and_structured_output(
        self, test_db, price_cache, monkeypatch
    ):
        captured = {}
        _stub_completion(monkeypatch, '{"message": "Hi"}', captured)

        await service.chat_with_llm("hello", price_cache)

        assert captured["model"] == "openrouter/openai/gpt-oss-120b"
        assert captured["extra_body"] == {"provider": {"order": ["cerebras"]}}
        assert captured["response_format"] is service.LlmResponse
        assert captured["reasoning_effort"] == "low"

    async def test_response_is_returned_to_caller(self, test_db, price_cache, monkeypatch):
        _stub_completion(monkeypatch, '{"message": "All good"}')
        result = await service.chat_with_llm("hello", price_cache)
        assert result == {"message": "All good", "trades": [], "watchlist_changes": []}


class TestPromptConstruction:
    async def test_system_prompt_carries_portfolio_context(
        self, test_db, price_cache, monkeypatch
    ):
        await upsert_position("AAPL", 10, 180.0)
        captured = {}
        _stub_completion(monkeypatch, '{"message": "Hi"}', captured)

        await service.chat_with_llm("how am I doing?", price_cache)

        system = captured["messages"][0]
        assert system["role"] == "system"
        assert "FinAlly" in system["content"]
        assert "AAPL" in system["content"]
        assert "$+105.00" in system["content"]  # (190.50 - 180) * 10 unrealized P&L
        assert "Watchlist:" in system["content"]

    async def test_conversation_history_is_included_in_order(
        self, test_db, price_cache, monkeypatch
    ):
        await insert_chat_message(role="user", content="what do I own?")
        await insert_chat_message(role="assistant", content="Nothing yet.")
        captured = {}
        _stub_completion(monkeypatch, '{"message": "Hi"}', captured)

        await service.chat_with_llm("ok thanks", price_cache)

        roles_and_content = [(m["role"], m["content"]) for m in captured["messages"]]
        assert roles_and_content[1] == ("user", "what do I own?")
        assert roles_and_content[2] == ("assistant", "Nothing yet.")
        assert roles_and_content[-1] == ("user", "ok thanks")

    async def test_current_message_is_not_duplicated(self, test_db, price_cache, monkeypatch):
        # The chat route persists the user message before calling the service.
        await insert_chat_message(role="user", content="buy something")
        captured = {}
        _stub_completion(monkeypatch, '{"message": "Hi"}', captured)

        await service.chat_with_llm("buy something", price_cache)

        user_turns = [m for m in captured["messages"] if m["role"] == "user"]
        assert user_turns == [{"role": "user", "content": "buy something"}]

    async def test_history_is_capped(self, test_db, price_cache, monkeypatch):
        for i in range(30):
            await insert_chat_message(role="user", content=f"msg {i}")
        captured = {}
        _stub_completion(monkeypatch, '{"message": "Hi"}', captured)

        await service.chat_with_llm("latest", price_cache)

        # system + at most HISTORY_LIMIT history turns + the new user message
        assert len(captured["messages"]) <= service.HISTORY_LIMIT + 2


class TestGracefulDegradation:
    async def test_api_failure_returns_friendly_message(
        self, test_db, price_cache, monkeypatch
    ):
        def boom(**kwargs):
            raise RuntimeError("upstream 503")

        monkeypatch.setattr(service, "completion", boom)

        result = await service.chat_with_llm("hello", price_cache)
        assert result["message"] == service.ERROR_MESSAGE
        assert result["trades"] == []

    async def test_unparseable_response_does_not_raise(
        self, test_db, price_cache, monkeypatch
    ):
        _stub_completion(monkeypatch, '{"trades": []}')  # no message field
        result = await service.chat_with_llm("hello", price_cache)
        assert "garbled" in result["message"]
        assert result["trades"] == []

    async def test_prose_response_is_shown_and_executes_nothing(
        self, test_db, price_cache, monkeypatch
    ):
        _stub_completion(monkeypatch, "You are well diversified.")
        result = await service.chat_with_llm("hello", price_cache)
        assert result["message"] == "You are well diversified."
        assert result["trades"] == []

    async def test_missing_api_key_returns_configuration_message(
        self, test_db, price_cache, monkeypatch
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "")
        result = await service.chat_with_llm("hello", price_cache)
        assert result["message"] == service.NO_KEY_MESSAGE

    async def test_malformed_trade_is_dropped_but_good_one_executes(
        self, test_db, price_cache, monkeypatch
    ):
        _stub_completion(
            monkeypatch,
            '{"message": "Mixed", "trades": ['
            '{"ticker": "AAPL", "side": "hold", "quantity": 1}, '
            '{"ticker": "MSFT", "side": "buy", "quantity": 1}]}',
        )
        result = await service.chat_with_llm("do it", price_cache)
        assert len(result["trades"]) == 1
        assert result["trades"][0]["ticker"] == "MSFT"
        assert result["trades"][0]["status"] == "executed"


class TestMockModeShortCircuits:
    async def test_mock_mode_never_calls_the_model(self, test_db, price_cache, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        # `no_network` is still active: a real call would fail the test.
        result = await service.chat_with_llm("hello", price_cache)
        assert "trading assistant" in result["message"]

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", " true ", "1", "yes"])
    def test_truthy_values_enable_mock(self, monkeypatch, value):
        monkeypatch.setenv("LLM_MOCK", value)
        assert service.mock_mode_enabled() is True

    @pytest.mark.parametrize("value", ["", "false", "False", "0", "no", "maybe"])
    def test_other_values_do_not(self, monkeypatch, value):
        monkeypatch.setenv("LLM_MOCK", value)
        assert service.mock_mode_enabled() is False
