# llm-engineer — updated 2026-08-31T00:00:00Z
State: done

Done:
- Audited the existing `backend/app/llm/**`. Confirmed it calls LiteLLM -> OpenRouter
  with `openrouter/openai/gpt-oss-120b`, `extra_body={"provider": {"order": ["cerebras"]}}`,
  `reasoning_effort="low"` and `response_format=LlmResponse` per the `cerebras` skill.
- `models.py`: `side` and `action` are now `Literal` enums (so the JSON schema sent to
  the provider constrains them) with before-validators that normalize case/whitespace,
  so "BUY"/" Sell " still parse. Blank `message` is rejected in a validator rather than
  with `min_length`, because provider strict mode rejects `minLength` in the schema.
- New `parsing.py`: strict parse first, then graceful degradation — strips ``` fences,
  extracts the outermost balanced JSON object (string-aware), drops individually
  malformed actions while keeping the message, and falls back to showing plain prose
  with zero actions executed. Only raises when nothing is recoverable.
- `service.py`:
  - Portfolio context now comes from `app.routes.trading.value_portfolio`, so the model
    sees exactly the numbers `GET /api/portfolio` reports (positions with avg cost,
    current price, market value, unrealized P&L and %, cash, total value, watchlist
    with live quotes).
  - Conversation history loaded from `chat_messages`, capped at 20 turns, and the
    just-persisted copy of the incoming message is dropped (the chat route stores the
    user message before calling us, so it was being sent to the model twice).
  - Trades and watchlist changes auto-execute through `execute_trade_order` — the same
    validation path as manual trades. Failures come back as `error` entries per action.
  - The blocking `completion()` call now runs in `asyncio.to_thread`, so an LLM call no
    longer stalls the event loop (and with it the SSE price stream) for its duration.
  - Missing `OPENROUTER_API_KEY` returns a clear configuration message instead of an
    exception; API failures and unparseable responses degrade to a friendly message.
- `mock.py`: deterministic, documented triggers (buy/sell/watch/unwatch/portfolio/
  greeting), fractional quantities, and remove-vs-add disambiguation. `LLM_MOCK` accepts
  1/true/yes case-insensitively.
- Tests: `backend/tests/llm/` is now 135 tests (models, parsing, mock triggers, action
  execution, prompt construction, degradation). No live API calls — `service.completion`
  is monkeypatched and an autouse guard fails any test that reaches the real one.
- `backend/tests/llm/test_e2e_contract.py` pins the six LLM_MOCK inputs that
  `test/e2e/chat.spec.ts` sends, plus the response-shape keys the frontend branches on,
  so a reworded mock reply fails the fast backend suite rather than the browser suite.

Verified: `cd backend && uv run pytest` -> 362 passed. `ruff check app/llm tests/llm` clean.

In flight: nothing.

Blocked on: nothing.

Interface changes:
- None to the wire format. `POST /api/chat` still returns
  `{message, trades[], watchlist_changes[]}`; action entries still carry either
  `status: "executed"`/`"done"` or `error`.
- I initially added `app/trading.py`; backend-api-engineer landed the equivalent
  `app/routes/trading.py` in their own area, so mine was deleted and the LLM service
  now imports theirs. There is one trade executor.
- Mock trigger phrases are pinned by `backend/tests/llm/test_mock.py` and
  `test_e2e_contract.py` — E2E chat tests can rely on them; changing one fails a unit
  test first. Confirmed with integration-tester; ping me before rewording a mock reply.
- Uses the DB engineer's typed `DuplicateTickerError`. Trade auto-execution delegates to
  `app/routes/trading.py::execute_trade_order` (which now delegates to the atomic
  `app.db.apply_trade`), so LLM and manual trades share one validation path and one
  transaction. The LLM layer records no snapshots of its own — the executor does it —
  and makes no market-data-source calls; the chat route reconciles the source.
