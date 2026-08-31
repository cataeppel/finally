import {
  addToWatchlist,
  executeTrade,
  getChatHistory,
  getPortfolio,
  getWatchlist,
  removeFromWatchlist,
  sendChatMessage,
} from "@/lib/api";

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("getPortfolio", () => {
  it("maps the backend's `cash` field onto `cash_balance`", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ positions: [], cash: 9500.25, total_value: 10100, unrealized_pnl: 12.5 })
    );

    const portfolio = await getPortfolio();

    expect(mockFetch).toHaveBeenCalledWith("/api/portfolio", expect.anything());
    expect(portfolio.cash_balance).toBe(9500.25);
    expect(portfolio.total_value).toBe(10100);
    expect(portfolio.unrealized_pnl).toBe(12.5);
  });
});

describe("executeTrade", () => {
  it("posts the order and unwraps the trade record", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({
        trade: {
          id: "t1",
          ticker: "AAPL",
          side: "buy",
          quantity: 5,
          price: 190.5,
          executed_at: "2026-01-01T00:00:00Z",
        },
      })
    );

    const trade = await executeTrade({ ticker: "AAPL", side: "buy", quantity: 5 });

    const [path, init] = mockFetch.mock.calls[0];
    expect(path).toBe("/api/portfolio/trade");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ ticker: "AAPL", side: "buy", quantity: 5 });
    expect(trade.price).toBe(190.5);
  });

  it("surfaces the FastAPI `detail` string rather than the raw body", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ detail: "Insufficient cash. Need $999.00, have $10.00" }, false, 400)
    );

    await expect(
      executeTrade({ ticker: "AAPL", side: "buy", quantity: 500 })
    ).rejects.toThrow("Insufficient cash. Need $999.00, have $10.00");
  });

  it("falls back to the raw body when the error is not JSON", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "Internal Server Error",
      json: async () => ({}),
    });

    await expect(
      executeTrade({ ticker: "AAPL", side: "buy", quantity: 1 })
    ).rejects.toThrow("Internal Server Error");
  });
});

describe("watchlist", () => {
  it("unwraps the watchlist array", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ watchlist: [{ ticker: "AAPL" }] }));
    await expect(getWatchlist()).resolves.toEqual([{ ticker: "AAPL" }]);
  });

  it("posts a ticker to add", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ticker: "PYPL" }));
    await addToWatchlist("PYPL");

    const [path, init] = mockFetch.mock.calls[0];
    expect(path).toBe("/api/watchlist");
    expect(JSON.parse(init.body)).toEqual({ ticker: "PYPL" });
  });

  it("deletes a ticker by path", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ removed: true }));
    await removeFromWatchlist("NFLX");

    const [path, init] = mockFetch.mock.calls[0];
    expect(path).toBe("/api/watchlist/NFLX");
    expect(init.method).toBe("DELETE");
  });
});

describe("sendChatMessage", () => {
  it("keeps only executed trades and collects action errors", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({
        message: "Bought AAPL, could not sell TSLA.",
        trades: [
          { ticker: "AAPL", side: "buy", quantity: 10, price: 190.5, status: "executed" },
          { ticker: "TSLA", side: "sell", quantity: 5, price: 0, error: "Insufficient shares" },
        ],
        watchlist_changes: [{ ticker: "PYPL", action: "add", status: "applied" }],
      })
    );

    const { message } = await sendChatMessage("buy 10 AAPL");

    expect(message.role).toBe("assistant");
    expect(message.content).toBe("Bought AAPL, could not sell TSLA.");
    expect(message.actions?.trades).toHaveLength(1);
    expect(message.actions?.trades?.[0].ticker).toBe("AAPL");
    expect(message.actions?.watchlist_changes).toEqual([{ ticker: "PYPL", action: "add" }]);
    expect(message.actions?.errors).toEqual(["Insufficient shares"]);
  });

  it("leaves actions undefined when the reply is conversational only", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ message: "I'm FinAlly.", trades: [], watchlist_changes: [] })
    );

    const { message } = await sendChatMessage("hello");
    expect(message.actions).toBeUndefined();
  });

  it("tolerates a response with no action arrays at all", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ message: "Hi." }));
    const { message } = await sendChatMessage("hi");
    expect(message.content).toBe("Hi.");
    expect(message.actions).toBeUndefined();
  });
});

describe("getChatHistory", () => {
  it("parses the stored actions JSON string", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({
        messages: [
          {
            id: "m1",
            role: "user",
            content: "buy 10 AAPL",
            actions: null,
            created_at: "2026-01-01T00:00:00Z",
          },
          {
            id: "m2",
            role: "assistant",
            content: "Done.",
            actions: JSON.stringify({
              trades: [
                { ticker: "AAPL", side: "buy", quantity: 10, price: 190.5, status: "executed" },
              ],
              watchlist_changes: [],
            }),
            created_at: "2026-01-01T00:00:01Z",
          },
        ],
      })
    );

    const history = await getChatHistory();

    expect(history).toHaveLength(2);
    expect(history[0].actions).toBeUndefined();
    expect(history[1].actions?.trades?.[0].quantity).toBe(10);
  });

  it("ignores malformed stored actions rather than throwing", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({
        messages: [
          {
            id: "m1",
            role: "assistant",
            content: "Done.",
            actions: "{not json",
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      })
    );

    const history = await getChatHistory();
    expect(history[0].actions).toBeUndefined();
  });
});
