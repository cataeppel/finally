# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, seeded from `/api/history/{ticker}` on load and then extended live from the SSE stream
- **Click a ticker** to see a larger detailed chart in the main chart area — also seeded from `/api/history/{ticker}`, so a page reload never shows an empty chart
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting *or* degraded, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Never colour alone**: every P&L and change figure carries an explicit `+`/`−` sign and a ▲/▼ glyph alongside the green/red. Red/green-only encoding is the most common colourblind failure case, and the signs read as more professional anyway
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

### Background Tasks

Exactly two background tasks run for the lifetime of the process:

1. **Market data producer** (~500ms) — simulator or Massive poller, writing to the shared price cache
2. **Portfolio snapshot recorder** (30s) — writing to `portfolio_snapshots`

### Concurrency & SQLite

Both background tasks and every request handler touch the database from a single async process. To avoid intermittent `database is locked` errors — the most likely cause of flaky E2E runs:

- Open connections with `check_same_thread=False`, and enable **WAL mode** (`PRAGMA journal_mode=WAL`) and a busy timeout (`PRAGMA busy_timeout=5000`)
- The SQLite driver is blocking — run database work off the event loop (`asyncio.to_thread`) so a slow write never stalls the SSE stream
- Serialize writes behind a single lock; reads run concurrently under WAL

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the **bind-mount target**. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts because the host directory is mounted into the container at `/app/db`. A bind mount rather than a named Docker volume is deliberate — see §11.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- **Inside the container, environment variables are the only source of configuration.** The `.env` file is never copied into the image — `docker run --env-file .env` injects its values. `python-dotenv` reading `.env` from the project root is a **local-development convenience only**, so the backend also runs outside Docker

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.) — see **Seed Prices for Unknown Tickers**
- Runs as an in-process background task — no external dependencies

### Seed Prices for Unknown Tickers

The watchlist is editable by both the user and the LLM, so the simulator must be able to price a ticker it has never seen.

- A lookup table of ~50 well-known US tickers with realistic seed prices covers the common cases — the ten defaults plus likely additions (PYPL, AMD, INTC, DIS, BA, WMT, KO, …)
- Any ticker outside the table gets a **deterministic** seed price derived from a hash of the symbol, mapped into the $20–$500 range, with default drift and volatility. Deterministic means the same symbol always starts at the same price, which keeps E2E tests reproducible
- Unknown tickers are **never rejected** on the grounds of being unknown. The LLM is encouraged to manage the watchlist proactively, and a rejection mid-conversation is a worse experience than a plausible synthetic price. Format validation still applies (see §8)

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all watched tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

**Failure handling.** On a `429`, an auth error, or a network timeout the poller logs, keeps the last cached prices, and retries with exponential backoff capped at 60s. After three consecutive failures it **falls back to the simulator** for the remainder of the process lifetime and marks the stream `degraded`, which the frontend shows as a yellow connection dot. The SSE stream must never die because the upstream did.

**Why the simulator is the default.** Polygon's free tier is delayed or end-of-day rather than real-time, and outside US market hours real quotes do not move at all — a "real data" demo can look completely dead. The simulator always produces motion, which is what makes the terminal worth watching. Setting `MASSIVE_API_KEY` makes the data *real*, not *better*.

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds, per ticker: latest price, previous price, **`session_open`** (the first price seen after process start — the reference for daily change %), last update timestamp, and a **ring buffer of the most recent 600 ticks** (~5 minutes at 500ms)
- The ring buffer backs `GET /api/history/{ticker}`, so charts and sparklines render populated on page load instead of starting from a single point
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

**Tracked ticker set.** The set of tickers the producer maintains is **`watchlist ∪ {tickers with a non-zero position}`** — *not* the watchlist alone. It is recomputed whenever the watchlist or positions change. Without the union, removing a held ticker from the watchlist would freeze its price and silently corrupt the positions table, the heatmap and total portfolio value.

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes price updates for every ticker in the **tracked set** (above) at a regular cadence (~500ms)
- Each SSE event is a JSON object with this exact shape:

```json
{
  "ticker": "AAPL",
  "price": 191.24,
  "prev_price": 191.19,
  "open": 190.02,
  "change": 1.22,
  "change_pct": 0.64,
  "direction": "up",
  "ts": "2026-08-26T14:03:07.412Z"
}
```

- `direction` (`"up"` / `"down"` / `"flat"`) compares against `prev_price` — the previous tick — and drives the flash animation
- `change` and `change_pct` compare against `open` — the session open — and drive the "daily change %" column. These two references are different on purpose; conflating them makes the change column read ±0.05% forever and look broken
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Conventions

**Timestamps.** Every `TEXT` timestamp column, and every timestamp in an API or SSE payload, is **UTC ISO-8601 with a `Z` suffix and millisecond precision** — `2026-08-26T14:03:07.412Z`. This makes lexicographic ordering identical to chronological ordering, so `ORDER BY recorded_at` is correct without parsing, and gives the frontend exactly one format to handle.

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**user_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution.

> **A trade does not move total portfolio value.** Market orders with no fees mean cash falls by exactly the value of the shares acquired. The post-trade snapshot marks *when* a trade happened, not a jump in value. Do not write a test asserting that P&L moves on a trade — it will fail against correct code.
>
> **Retention:** the snapshot task deletes rows older than 7 days on each write, capping the table at ~20k rows. Container restarts leave gaps, which render as straight line segments in the P&L chart; this is expected, not a bug.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One row in `user_profile`: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates |
| GET | `/api/history/{ticker}` | Recent price ticks from the in-memory ring buffer (up to ~600 points) for seeding charts and sparklines |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots for the P&L chart. `?since=` (ISO timestamp) and `?limit=`; defaults to the last 24 hours |
| GET | `/api/portfolio/trades` | Trade history, newest first. `?limit=` (default 100) |
| POST | `/api/portfolio/reset` | Reset to seeded state: $10,000 cash, no positions, no trades, default watchlist |

### Trade Execution Rules

These rules apply identically to manual trades and to trades the LLM auto-executes.

- Market orders only, instant fill at the current cached price, no fees, no slippage
- `quantity` must be **> 0** — zero, negative, and non-numeric values are rejected with `400`. Fractional shares are supported down to `1e-6`
- A buy requires `cash_balance >= quantity * price`; a sell requires `position.quantity >= quantity`. Failures return `400` with a human-readable `detail`
- **A sell never changes `avg_cost`.** Cost basis moves on buys only: `avg_cost = (avg_cost * qty_old + price * qty_bought) / (qty_old + qty_bought)`
- After every trade, round `quantity` to 8 decimal places and `cash_balance` to 2. If the resulting position quantity is **`< 1e-6`, delete the position row** — otherwise floating-point residue leaves a phantom position that can never be closed
- Trading a ticker that is not in the watchlist is allowed (the trade bar takes free text). The ticker enters the tracked set (§6) so its price streams; it is **not** auto-added to the watchlist
- **Realized P&L is not stored.** Only unrealized P&L is computed. The `trades` log is exposed via `GET /api/portfolio/trades` so the UI and the LLM can reason about closed positions from the raw history

### Trade History

`GET /api/portfolio/trades` exists so the `trades` table is not write-only — without it, nothing in the system ever reads what the user has already bought and sold, and the LLM's answer to "how am I doing?" would silently ignore every closed position.

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

### Watchlist Rules

- Tickers are **trimmed and uppercased** on input and must match `^[A-Z]{1,5}$`; anything else returns `400`
- **Maximum 30 tickers.** Adding beyond the cap returns `400`. The LLM manages the watchlist proactively, so an explicit cap is what bounds the simulator loop and the SSE payload size
- Adding a duplicate is idempotent — `200`, no new row, per the `(user_id, ticker)` unique constraint
- Removing a ticker you hold a position in is allowed. The position is unaffected and its price keeps streaming (§6, tracked ticker set)

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions + errors) |
| GET | `/api/chat/history` | Past conversation, oldest first. `?limit=` (default 50) — the frontend calls this on load so a reload does not blank the chat panel |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the **last 20 messages** of conversation history from the `chat_messages` table, oldest trimmed first
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response, collecting failures into an `errors` array
7. Stores the message, the executed actions, and any errors in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (see **Trade Execution Rules**, §8)
- `watchlist_changes` (optional): Array of watchlist modifications

### API Response Envelope

`POST /api/chat` returns the LLM's structured output plus the *results* of executing it:

```json
{
  "message": "Your tech concentration is high — I've trimmed NVDA.",
  "trades": [{"ticker": "NVDA", "side": "sell", "quantity": 5, "price": 118.40, "status": "executed"}],
  "watchlist_changes": [],
  "errors": [
    {"action": "trade", "ticker": "AAPL", "detail": "Insufficient cash: need $1,900.00, have $1,240.13"}
  ]
}
```

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

**Reporting failures.** The LLM writes its `message` *before* the backend executes anything, so it cannot know that a trade failed — it may cheerfully announce a purchase that never happened. Rather than pay for a second LLM round-trip, failures are reported structurally:

- Every requested action runs through the same validation as a manual trade (§8)
- Successful actions are echoed back with their fill price and stored in `chat_messages.actions`
- Failed actions append to the `errors` array with a human-readable `detail`
- **The frontend renders `errors` inline directly beneath the assistant message**, as a visually distinct system note (e.g. "⚠ Trade not executed — insufficient cash"), clearly not part of the LLM's own text. The assistant may claim it bought something; the note immediately below corrects the record
- Errors are persisted alongside the actions, so the correction survives a reload, and they are included in the next turn's context so the LLM can self-correct

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### Structured Output Reliability

- Request `response_format` with a JSON schema through LiteLLM. **Verify this early**: confirm that `openrouter/openai/gpt-oss-120b` with the Cerebras provider actually honours schema-constrained decoding through OpenRouter. Provider support for structured outputs varies, and this is the single riskiest external assumption in the build
- **Fallback if it does not**: specify the schema in the system prompt, then parse with `json.loads` after stripping any markdown code fence
- **On a parse failure**: retry **once**, appending the malformed text plus a short repair instruction. If the retry also fails, return `{"message": "I couldn't process that — could you rephrase?", "trades": [], "watchlist_changes": [], "errors": [...]}`
- A bad LLM response must never surface as a `500`, and must never execute a partially-parsed trade

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

The mapping below is a **contract between the backend and the E2E suite** — the tests assert against it, so it belongs in the plan rather than in one agent's head. Matching is case-insensitive and evaluated in order:

| User message contains | Mock response |
|---|---|
| `buy` | `{"message": "Bought 5 AAPL.", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5}]}` |
| `sell` | `{"message": "Sold 5 AAPL.", "trades": [{"ticker": "AAPL", "side": "sell", "quantity": 5}]}` |
| `watch` or `add` | `{"message": "Added PYPL to your watchlist.", "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}` |
| *anything else* | `{"message": "Your portfolio is concentrated in technology. Consider diversifying.", "trades": [], "watchlist_changes": []}` |

Mock responses never call OpenRouter, never fail, and still pass through real trade validation — so an E2E test can assert on a *failed* auto-trade by draining the cash balance first.

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), daily change % (from `change_pct` in the SSE payload, measured against the **session open** — not the previous tick), and a sparkline mini-chart seeded from `/api/history/{ticker}` then extended from SSE
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Seeded from `/api/history/{ticker}` on selection so it is never empty, then extended live from SSE. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, from `GET /api/portfolio/history` (last 24 hours by default). Gaps from container restarts render as straight segments — expected
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history restored on load from `GET /api/chat/history`, loading indicator while waiting for the LLM. Trade executions and watchlist changes shown inline as confirmations; **failed actions from the `errors` array shown inline as distinct warning notes** beneath the assistant message.
- **Header** — portfolio total value, connection status indicator, cash balance. **Total value is recomputed client-side on every price tick** as `cash + Σ(quantity × latest SSE price)` — no polling. `GET /api/portfolio` is the source of truth for cash and cost basis and is re-fetched only after a trade or a chat action
- **Connection indicator** — green = connected; **yellow = reconnecting *or* degraded** (the Massive poller failed and fell back to the simulator); red = disconnected

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Canvas-based charting library preferred (Lightweight Charts or Recharts) for performance
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it — driven by `direction`, which compares against the previous tick
- Every P&L and change figure renders with an explicit `+`/`−` sign and a ▲/▼ glyph in addition to green/red
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

- **Route ordering matters.** Register every `/api/*` route **first**, then mount the static export last. A catch-all mounted at `/` will shadow every API route if registered first — a confusing failure where the frontend loads but every request returns HTML
- Unknown non-API paths fall back to `index.html` so refreshes and client-side navigation work
- Add a `HEALTHCHECK` instruction hitting `GET /api/health`, so `docker ps` reports real health and the start scripts can poll for readiness instead of sleeping a fixed interval

### Docker Volume

The SQLite database persists via a **bind mount** of the repo's `db/` directory:

```bash
docker run -v "$(pwd)/db:/app/db" -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container, and the backend writes `finally.db` there. A bind mount rather than a named volume is deliberate: students can open the database with any SQLite tool, and **resetting is `rm db/finally.db` plus a restart** (or `POST /api/portfolio/reset` without one). `db/finally.db` is gitignored; `db/.gitkeep` keeps the directory in the repo.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

> ⚠ **There is no authentication, and a single hardcoded `"default"` user.** On a public URL every visitor shares one portfolio, and anyone who finds it can spend the deployer's OpenRouter credits through the chat panel. Fine for localhost and for a short-lived demo link; not fine as a permanent public deployment.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss), and every rule in **Trade Execution Rules** (§8) — `avg_cost` unchanged by sells, position row deleted when quantity drops below `1e-6`, zero/negative quantities rejected with `400`
- LLM: structured output parsing handles all valid schemas, the retry-then-fallback path on malformed responses, trade validation within the chat flow, and the `errors` array populated when an auto-trade fails
- API routes: correct status codes, response shapes, error handling

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases; selling the full quantity **deletes** the position row
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points. Assert on position and cash changes, **not** on total value moving after a trade — it does not (§7)
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- SSE resilience: disconnect and verify reconnection
- Reload persistence: `GET /api/chat/history` restores the conversation, `GET /api/history/{ticker}` seeds a populated chart
- Held-but-unwatched ticker: buy a ticker, remove it from the watchlist, verify its price still streams and its position stays live
