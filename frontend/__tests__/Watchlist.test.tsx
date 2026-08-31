import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Watchlist from "@/components/Watchlist";
import type { PriceMap, PricePoint, WatchlistItem } from "@/lib/types";
import { addToWatchlist, removeFromWatchlist } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  addToWatchlist: jest.fn(),
  removeFromWatchlist: jest.fn(),
}));

const mockAdd = addToWatchlist as jest.MockedFunction<typeof addToWatchlist>;
const mockRemove = removeFromWatchlist as jest.MockedFunction<typeof removeFromWatchlist>;

const items: WatchlistItem[] = [{ ticker: "AAPL" }, { ticker: "TSLA" }];

function priceMap(ticker: string, price: number, previous: number): PriceMap {
  return {
    [ticker]: {
      ticker,
      price,
      previous_price: previous,
      timestamp: 1_700_000_000,
      change: price - previous,
      change_percent: ((price - previous) / previous) * 100,
      direction: price > previous ? "up" : price < previous ? "down" : "flat",
    },
  };
}

function renderList(overrides: Partial<React.ComponentProps<typeof Watchlist>> = {}) {
  const props = {
    items,
    prices: {} as PriceMap,
    getHistory: (): PricePoint[] => [],
    selectedTicker: null,
    onSelectTicker: jest.fn(),
    onRefresh: jest.fn(),
    ...overrides,
  };
  const view = render(<Watchlist {...props} />);
  return { ...view, props };
}

beforeEach(() => {
  mockAdd.mockReset().mockResolvedValue(undefined);
  mockRemove.mockReset().mockResolvedValue(undefined);
});

describe("Watchlist", () => {
  it("renders a row per watched ticker", () => {
    renderList();
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-row-TSLA")).toBeInTheDocument();
  });

  it("prefers the live streamed price over the REST snapshot", () => {
    renderList({
      items: [{ ticker: "AAPL", price: 100 }],
      prices: priceMap("AAPL", 190.5, 190),
    });
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("190.50");
  });

  it("shows an empty state when nothing is watched", () => {
    renderList({ items: [] });
    expect(screen.getByText(/Watchlist is empty/)).toBeInTheDocument();
  });

  it("adds a ticker, uppercased, and refreshes", async () => {
    const user = userEvent.setup();
    const { props } = renderList();

    await user.type(screen.getByTestId("watchlist-add-input"), "pypl");
    await user.click(screen.getByTestId("watchlist-add-button"));

    await waitFor(() => expect(mockAdd).toHaveBeenCalledWith("PYPL"));
    expect(props.onRefresh).toHaveBeenCalled();
  });

  it("adds a ticker on Enter", async () => {
    const user = userEvent.setup();
    renderList();

    await user.type(screen.getByTestId("watchlist-add-input"), "NFLX{Enter}");

    await waitFor(() => expect(mockAdd).toHaveBeenCalledWith("NFLX"));
  });

  it("shows an error when adding fails and does not clear the input", async () => {
    const user = userEvent.setup();
    mockAdd.mockRejectedValue(new Error("AAPL already in watchlist"));
    renderList();

    await user.type(screen.getByTestId("watchlist-add-input"), "AAPL{Enter}");

    expect(await screen.findByTestId("watchlist-error")).toHaveTextContent(
      "AAPL already in watchlist"
    );
    expect(screen.getByTestId("watchlist-add-input")).toHaveValue("AAPL");
  });

  it("removes a ticker without selecting the row", async () => {
    const user = userEvent.setup();
    const { props } = renderList();

    await user.click(screen.getByTestId("watchlist-remove-AAPL"));

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith("AAPL"));
    expect(props.onRefresh).toHaveBeenCalled();
    expect(props.onSelectTicker).not.toHaveBeenCalled();
  });

  it("selects a ticker when its row is clicked", async () => {
    const user = userEvent.setup();
    const { props } = renderList();

    await user.click(screen.getByTestId("watchlist-row-TSLA"));

    expect(props.onSelectTicker).toHaveBeenCalledWith("TSLA");
  });

  it("applies the up-flash class when a price ticks higher", () => {
    const { rerender, props } = renderList({ prices: priceMap("AAPL", 190, 190) });
    const row = screen.getByTestId("watchlist-row-AAPL");
    expect(row.className).not.toMatch(/price-flash/);

    rerender(<Watchlist {...props} prices={priceMap("AAPL", 191, 190)} />);

    expect(row).toHaveClass("price-flash-up");
    expect(row).not.toHaveClass("price-flash-down");
  });

  it("applies the down-flash class when a price ticks lower", () => {
    const { rerender, props } = renderList({ prices: priceMap("AAPL", 190, 190) });
    const row = screen.getByTestId("watchlist-row-AAPL");

    rerender(<Watchlist {...props} prices={priceMap("AAPL", 189, 190)} />);

    expect(row).toHaveClass("price-flash-down");
  });

  it("does not flash when the price is unchanged", () => {
    const { rerender, props } = renderList({ prices: priceMap("AAPL", 190, 190) });
    const row = screen.getByTestId("watchlist-row-AAPL");

    rerender(<Watchlist {...props} prices={priceMap("AAPL", 190, 190)} />);

    expect(row.className).not.toMatch(/price-flash/);
  });

  it("renders a sparkline once two samples have accumulated", () => {
    renderList({
      getHistory: (ticker) =>
        ticker === "AAPL"
          ? [
              { time: 1, price: 100 },
              { time: 2, price: 105 },
            ]
          : [],
    });

    const spark = screen.getByTestId("watchlist-sparkline-AAPL");
    expect(spark.tagName.toLowerCase()).toBe("svg");
    expect(spark.querySelector("polyline")).toBeInTheDocument();
    // TSLA has no history yet, so it renders a placeholder rather than an svg.
    expect(screen.getByTestId("watchlist-sparkline-TSLA").tagName.toLowerCase()).toBe("div");
  });

  it("shows change since the session opened once history exists", () => {
    renderList({
      items: [{ ticker: "AAPL" }],
      prices: priceMap("AAPL", 110, 109.99),
      getHistory: () => [
        { time: 1, price: 100 },
        { time: 2, price: 105 },
        { time: 3, price: 110 },
      ],
    });

    // 100 -> 110 since page load, not the +0.01% of the last tick.
    const row = screen.getByTestId("watchlist-row-AAPL");
    expect(row).toHaveTextContent("+10.00%");
  });

  it("falls back to the streamed tick change before history accumulates", () => {
    renderList({
      items: [{ ticker: "AAPL" }],
      prices: priceMap("AAPL", 101, 100),
      getHistory: () => [],
    });

    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+1.00%");
  });
});
