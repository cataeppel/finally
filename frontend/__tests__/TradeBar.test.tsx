import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TradeBar from "@/components/TradeBar";
import type { PriceMap } from "@/lib/types";
import { executeTrade } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  executeTrade: jest.fn(),
}));

const mockExecuteTrade = executeTrade as jest.MockedFunction<typeof executeTrade>;

const prices: PriceMap = {
  AAPL: {
    ticker: "AAPL",
    price: 190.5,
    previous_price: 190,
    timestamp: 1_700_000_000,
    change: 0.5,
    change_percent: 0.26,
    direction: "up",
  },
};

function renderBar(overrides: Partial<React.ComponentProps<typeof TradeBar>> = {}) {
  const onTradeExecuted = jest.fn();
  render(
    <TradeBar prices={prices} onTradeExecuted={onTradeExecuted} {...overrides} />
  );
  return { onTradeExecuted };
}

beforeEach(() => {
  mockExecuteTrade.mockReset();
});

describe("TradeBar", () => {
  it("shows the live price and estimated cost for the entered order", async () => {
    const user = userEvent.setup();
    renderBar();

    await user.type(screen.getByTestId("trade-ticker"), "aapl");
    await user.type(screen.getByTestId("trade-qty"), "2");

    expect(screen.getByTestId("trade-estimate")).toHaveTextContent("@ 190.50");
    expect(screen.getByTestId("trade-estimate")).toHaveTextContent("est. $381.00");
  });

  it("submits an uppercased buy order and reports the fill", async () => {
    const user = userEvent.setup();
    mockExecuteTrade.mockResolvedValue({
      id: "t1",
      ticker: "AAPL",
      side: "buy",
      quantity: 5,
      price: 190.5,
      executed_at: "2026-01-01T00:00:00Z",
    });
    const { onTradeExecuted } = renderBar();

    await user.type(screen.getByTestId("trade-ticker"), "aapl");
    await user.type(screen.getByTestId("trade-qty"), "5");
    await user.click(screen.getByTestId("trade-buy"));

    await waitFor(() =>
      expect(mockExecuteTrade).toHaveBeenCalledWith({
        ticker: "AAPL",
        quantity: 5,
        side: "buy",
      })
    );
    expect(await screen.findByTestId("trade-status")).toHaveTextContent(
      "BUY 5 AAPL @ 190.50"
    );
    expect(onTradeExecuted).toHaveBeenCalled();
  });

  it("submits a sell order", async () => {
    const user = userEvent.setup();
    mockExecuteTrade.mockResolvedValue({
      id: "t2",
      ticker: "AAPL",
      side: "sell",
      quantity: 3,
      price: 190.5,
      executed_at: "2026-01-01T00:00:00Z",
    });
    renderBar();

    await user.type(screen.getByTestId("trade-ticker"), "AAPL");
    await user.type(screen.getByTestId("trade-qty"), "3");
    await user.click(screen.getByTestId("trade-sell"));

    await waitFor(() =>
      expect(mockExecuteTrade).toHaveBeenCalledWith({
        ticker: "AAPL",
        quantity: 3,
        side: "sell",
      })
    );
    expect(await screen.findByTestId("trade-status")).toHaveTextContent("SELL 3 AAPL");
  });

  it("rejects an empty or non-positive quantity without calling the API", async () => {
    const user = userEvent.setup();
    renderBar();

    await user.type(screen.getByTestId("trade-ticker"), "AAPL");
    await user.click(screen.getByTestId("trade-buy"));

    expect(mockExecuteTrade).not.toHaveBeenCalled();
    expect(screen.getByTestId("trade-status")).toHaveTextContent(
      "Enter a valid ticker and quantity"
    );
  });

  it("surfaces the backend error message when a trade is rejected", async () => {
    const user = userEvent.setup();
    mockExecuteTrade.mockRejectedValue(new Error("Insufficient cash. Need $999.00"));
    renderBar();

    await user.type(screen.getByTestId("trade-ticker"), "AAPL");
    await user.type(screen.getByTestId("trade-qty"), "500");
    await user.click(screen.getByTestId("trade-buy"));

    expect(await screen.findByTestId("trade-status")).toHaveTextContent(
      "Insufficient cash. Need $999.00"
    );
  });

  it("prefills the ticker from the watchlist selection until the user types", async () => {
    const user = userEvent.setup();
    renderBar({ selectedTicker: "TSLA" });

    const tickerInput = screen.getByTestId("trade-ticker");
    expect(tickerInput).toHaveValue("TSLA");

    await user.clear(tickerInput);
    await user.type(tickerInput, "NVDA");
    expect(tickerInput).toHaveValue("NVDA");
  });
});
