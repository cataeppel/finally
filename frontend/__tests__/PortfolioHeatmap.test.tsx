import { render, screen } from "@testing-library/react";
import PortfolioHeatmap, { pnlColor } from "@/components/PortfolioHeatmap";
import type { Position } from "@/lib/types";

const mockPositions: Position[] = [
  {
    ticker: "AAPL",
    quantity: 10,
    avg_cost: 180,
    current_price: 190,
    unrealized_pnl: 100,
    pnl_percent: 5.56,
    market_value: 1900,
  },
  {
    ticker: "GOOGL",
    quantity: 5,
    avg_cost: 170,
    current_price: 165,
    unrealized_pnl: -25,
    pnl_percent: -2.94,
    market_value: 825,
  },
];

describe("PortfolioHeatmap", () => {
  it("shows empty message when no positions", () => {
    render(<PortfolioHeatmap positions={[]} />);
    expect(screen.getByText("No positions to display")).toBeInTheDocument();
  });

  it("renders ticker symbols in heatmap", () => {
    render(<PortfolioHeatmap positions={mockPositions} />);
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("GOOGL")).toBeInTheDocument();
  });

  it("renders P&L percentages", () => {
    render(<PortfolioHeatmap positions={mockPositions} />);
    expect(screen.getByText("+5.56%")).toBeInTheDocument();
    expect(screen.getByText("-2.94%")).toBeInTheDocument();
  });

  it("renders the heading", () => {
    render(<PortfolioHeatmap positions={mockPositions} />);
    expect(screen.getByText("Portfolio Heatmap")).toBeInTheDocument();
  });

  it("renders a tile per position, sized by market value", () => {
    render(<PortfolioHeatmap positions={mockPositions} />);

    const apple = screen.getByTestId("heatmap-tile-AAPL");
    const google = screen.getByTestId("heatmap-tile-GOOGL");
    expect(apple).toBeInTheDocument();
    expect(google).toBeInTheDocument();

    // AAPL holds 1900 of 2725 total value, so its tile must be the larger one.
    const width = (el: HTMLElement) => parseFloat(el.style.width);
    const height = (el: HTMLElement) => parseFloat(el.style.height);
    expect(width(apple) * height(apple)).toBeGreaterThan(width(google) * height(google));
  });

  it("colours tiles green for gains and red for losses", () => {
    render(<PortfolioHeatmap positions={mockPositions} />);
    expect(screen.getByTestId("heatmap-tile-AAPL")).toHaveStyle({
      backgroundColor: pnlColor(5.56),
    });
    expect(screen.getByTestId("heatmap-tile-GOOGL")).toHaveStyle({
      backgroundColor: pnlColor(-2.94),
    });
  });

  it("always renders the P&L percentage inside the tile, even when tiny", () => {
    // A long tail of small positions forces compact tiles; the percentage is
    // part of the heatmap contract and must survive the shrink.
    const many = Array.from({ length: 12 }, (_, i) => ({
      ticker: `T${i}`,
      quantity: 1,
      avg_cost: 100,
      current_price: 101,
      unrealized_pnl: 1,
      pnl_percent: i === 0 ? 50 : 1,
      market_value: i === 0 ? 5000 : 20,
    }));

    render(<PortfolioHeatmap positions={many} />);

    for (const pos of many) {
      const tile = screen.getByTestId(`heatmap-tile-${pos.ticker}`);
      expect(tile).toHaveTextContent(pos.ticker);
      expect(tile.textContent).toMatch(/[+-]\d+\.\d{2}%/);
    }
  });

  it("selects a ticker when its tile is clicked", () => {
    const onSelectTicker = jest.fn();
    render(
      <PortfolioHeatmap positions={mockPositions} onSelectTicker={onSelectTicker} />
    );
    screen.getByTestId("heatmap-tile-GOOGL").click();
    expect(onSelectTicker).toHaveBeenCalledWith("GOOGL");
  });
});

describe("pnlColor", () => {
  it("returns distinct greens as gains grow", () => {
    expect(pnlColor(10)).not.toBe(pnlColor(3));
    expect(pnlColor(3)).not.toBe(pnlColor(1));
  });

  it("returns distinct reds as losses grow", () => {
    expect(pnlColor(-10)).not.toBe(pnlColor(-3));
    expect(pnlColor(-3)).not.toBe(pnlColor(-1));
  });

  it("uses a neutral colour at exactly break-even", () => {
    expect(pnlColor(0)).toBe("#30363d");
  });
});
