import type {
  Portfolio,
  TradeRequest,
  TradeResponse,
  WatchlistItem,
  PortfolioSnapshot,
  ChatResponse,
  ChatMessage,
  ChatActions,
} from "./types";

const BASE = "/api";

/**
 * FastAPI reports errors as `{"detail": "..."}`. Surface just the detail so the
 * UI shows "Insufficient cash. Need $..." rather than `400: {"detail":"..."}`.
 */
function extractDetail(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed?.detail === "string") return parsed.detail;
    if (Array.isArray(parsed?.detail) && parsed.detail[0]?.msg) {
      return String(parsed.detail[0].msg);
    }
  } catch {
    // Not JSON — fall through to the raw body
  }
  return body || `Request failed (${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(extractDetail(res.status, await res.text()));
  }
  return res.json();
}

export async function getPortfolio(): Promise<Portfolio> {
  const data = await request<{
    positions: Portfolio["positions"];
    cash: number;
    total_value: number;
    unrealized_pnl?: number;
  }>("/portfolio");
  return {
    positions: data.positions,
    cash_balance: data.cash,
    total_value: data.total_value,
    unrealized_pnl: data.unrealized_pnl ?? 0,
  };
}

export async function executeTrade(trade: TradeRequest): Promise<TradeResponse> {
  const data = await request<{ trade: TradeResponse }>("/portfolio/trade", {
    method: "POST",
    body: JSON.stringify(trade),
  });
  return data.trade;
}

export async function getPortfolioHistory(): Promise<PortfolioSnapshot[]> {
  const data = await request<{ snapshots: PortfolioSnapshot[] }>("/portfolio/history");
  return data.snapshots;
}

export async function getWatchlist(): Promise<WatchlistItem[]> {
  const data = await request<{ watchlist: WatchlistItem[] }>("/watchlist");
  return data.watchlist;
}

export async function addToWatchlist(ticker: string): Promise<void> {
  await request("/watchlist", {
    method: "POST",
    body: JSON.stringify({ ticker }),
  });
}

export async function removeFromWatchlist(ticker: string): Promise<void> {
  await request(`/watchlist/${encodeURIComponent(ticker)}`, { method: "DELETE" });
}

/** Raw action shapes as returned by POST /api/chat */
interface RawTradeAction {
  ticker: string;
  side: string;
  quantity: number;
  price?: number;
  status?: string;
  error?: string;
}

interface RawWatchlistAction {
  ticker: string;
  action: string;
  status?: string;
  error?: string;
}

/** Fold the backend's action arrays into the ChatActions the UI renders. */
function toChatActions(
  trades: RawTradeAction[] = [],
  watchlistChanges: RawWatchlistAction[] = []
): ChatActions | undefined {
  const executed = trades
    .filter((t) => t.status === "executed")
    .map((t) => ({
      id: `${t.ticker}-${t.side}-${t.quantity}`,
      ticker: t.ticker,
      side: t.side as "buy" | "sell",
      quantity: t.quantity,
      price: t.price ?? 0,
      executed_at: new Date().toISOString(),
    }));

  const appliedWatchlist = watchlistChanges
    .filter((w) => !w.error)
    .map((w) => ({ ticker: w.ticker, action: w.action as "add" | "remove" }));

  const errors = [...trades, ...watchlistChanges]
    .filter((a) => a.error)
    .map((a) => a.error as string);

  if (!executed.length && !appliedWatchlist.length && !errors.length) {
    return undefined;
  }

  return {
    trades: executed.length ? executed : undefined,
    watchlist_changes: appliedWatchlist.length ? appliedWatchlist : undefined,
    errors: errors.length ? errors : undefined,
  };
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const data = await request<{
    message: string;
    trades?: RawTradeAction[];
    watchlist_changes?: RawWatchlistAction[];
  }>("/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
  });

  const assistantMessage: ChatMessage = {
    id: `assistant-${Date.now()}`,
    role: "assistant",
    content: data.message,
    actions: toChatActions(data.trades, data.watchlist_changes),
    created_at: new Date().toISOString(),
  };

  return { message: assistantMessage };
}

/**
 * Load persisted conversation history. The backend stores `actions` as a JSON
 * string (or null), so it is parsed back into ChatActions here.
 */
export async function getChatHistory(): Promise<ChatMessage[]> {
  const data = await request<{
    messages: Array<{
      id: string;
      role: "user" | "assistant";
      content: string;
      actions: string | null;
      created_at: string;
    }>;
  }>("/chat/history");

  return data.messages.map((m) => {
    let actions: ChatActions | undefined;
    if (m.actions) {
      try {
        const parsed = JSON.parse(m.actions);
        actions = toChatActions(parsed.trades, parsed.watchlist_changes);
      } catch {
        actions = undefined;
      }
    }
    return {
      id: m.id,
      role: m.role,
      content: m.content,
      actions,
      created_at: m.created_at,
    };
  });
}

export async function getHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
