# FinAlly Agent Team — Working Contract

This file is the shared contract for all team members. Read it before starting work.
The product spec is `planning/PLAN.md` — it is the source of truth for behaviour.
Market data is already complete (`planning/MARKET_DATA_SUMMARY.md`); do not redesign it.

## Current state (verify, don't assume)

A previous build pass produced a substantial amount of code across `frontend/`,
`backend/`, `test/`, `scripts/` and `Dockerfile`. Your job is to **complete and
correct** the project, not to rewrite it from scratch. Before changing anything in
your area: read what exists, run its tests, and only then decide what to add or fix.
Delete existing code only when it is genuinely wrong or redundant.

## Team members and file ownership

| Agent | Owns (may edit) |
|---|---|
| `frontend-engineer` | `frontend/**` |
| `backend-api-engineer` | `backend/app/main.py`, `backend/app/routes/**`, `backend/app/market/**`, `backend/tests/routes/**`, `backend/tests/market/**`, `backend/pyproject.toml` |
| `database-engineer` | `backend/app/db/**`, `backend/tests/db/**` |
| `llm-engineer` | `backend/app/llm/**`, `backend/tests/llm/**` |
| `integration-tester` | `test/**` |
| `devops-engineer` | `Dockerfile`, `.dockerignore`, `docker-compose*.yml`, `scripts/**`, `.env.example`, `.github/**` |

**Do not edit files you do not own.** If you need a change in someone else's area,
`SendMessage` that teammate with the exact ask (file, symbol, desired behaviour) and
continue with work that does not depend on it. Anyone may *read* any file.

Shared, non-owned files (`planning/PLAN.md`, `CLAUDE.md`, `README.md`): propose
changes to the team lead instead of editing.

## Interfaces you must not break unilaterally

- REST + SSE endpoint paths, request/response shapes: PLAN.md §8.
- DB schema and seed data: PLAN.md §7. The DB engineer owns the repository layer;
  route handlers call repository functions rather than writing SQL.
- LLM structured-output schema: PLAN.md §9.
- Environment variables: PLAN.md §5.

Any change to a shared interface must be (a) announced by `SendMessage` to every
affected teammate and (b) recorded in your status file before you land it.

## Status reporting

Keep `planning/status/<your-agent-name>.md` current — overwrite it, keep it short:

```
# <role> — updated <ISO timestamp>
State: in-progress | blocked | done
Done: <bullets>
In flight: <bullets>
Blocked on: <teammate + what you need, or "nothing">
Interface changes: <anything other agents must know, or "none">
```

Update it when you start, whenever you change a shared interface, when you become
blocked, and when you finish.

## Definition of done (whole project)

1. `cd backend && uv run pytest` — green.
2. `cd frontend && npm test` and `npm run build` (static export) — green.
3. `docker build` succeeds; the container serves the UI and API on port 8000.
4. `cd test && npx playwright test` against the container with `LLM_MOCK=true` — green,
   covering every scenario in PLAN.md §12.
5. The running app matches PLAN.md §2 and §10: live streaming prices with flash
   animations, sparklines, chart, heatmap, P&L chart, positions table, trade bar,
   AI chat, header with live total value and connection status.

## Working rules

- Write and run unit tests for your own area as you go; never hand off red tests.
- Run the commands, read the output, report faithfully. No "should work".
- Keep commits out of scope — the team lead handles git. Do not commit or push.
- Prefer small, verifiable increments over large speculative rewrites.
