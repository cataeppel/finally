import { render, screen } from "@testing-library/react";
import Header from "@/components/Header";

describe("Header", () => {
  it("renders portfolio value and cash balance", () => {
    render(
      <Header totalValue={12345.67} cashBalance={5000} status="connected" />
    );
    expect(screen.getByText("$12,345.67")).toBeInTheDocument();
    expect(screen.getByText("$5,000.00")).toBeInTheDocument();
  });

  it("renders FinAlly branding", () => {
    render(
      <Header totalValue={10000} cashBalance={10000} status="connected" />
    );
    expect(screen.getByText("FinAlly")).toBeInTheDocument();
  });

  it("shows Live when connected", () => {
    render(
      <Header totalValue={10000} cashBalance={10000} status="connected" />
    );
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("shows Connecting... when connecting", () => {
    render(
      <Header totalValue={10000} cashBalance={10000} status="connecting" />
    );
    expect(screen.getByText("Connecting...")).toBeInTheDocument();
  });

  it("shows Disconnected when disconnected", () => {
    render(
      <Header totalValue={10000} cashBalance={10000} status="disconnected" />
    );
    expect(screen.getByText("Disconnected")).toBeInTheDocument();
  });

  it("shows reconnecting while the stream retries", () => {
    render(<Header totalValue={10000} cashBalance={10000} status="reconnecting" />);
    expect(screen.getByTestId("connection-status")).toHaveTextContent("Reconnecting...");
  });

  it("exposes stable test hooks for the header figures", () => {
    render(
      <Header
        totalValue={12345.67}
        cashBalance={5000}
        unrealizedPnl={-250.5}
        status="connected"
      />
    );
    expect(screen.getByTestId("header-portfolio-value")).toHaveTextContent("$12,345.67");
    expect(screen.getByTestId("header-cash")).toHaveTextContent("$5,000.00");
    expect(screen.getByTestId("header-unrealized-pnl")).toHaveTextContent("-$250.50");
  });

  it("reports total return against the $10,000 starting equity", () => {
    render(<Header totalValue={11000} cashBalance={0} status="connected" />);
    expect(screen.getByTestId("header-total-return")).toHaveTextContent("+10.00%");
  });

  it("colours a losing portfolio red and a winning one green", () => {
    const { rerender } = render(
      <Header totalValue={9000} cashBalance={9000} status="connected" />
    );
    expect(screen.getByTestId("header-total-return")).toHaveClass("text-loss");

    rerender(<Header totalValue={11000} cashBalance={11000} status="connected" />);
    expect(screen.getByTestId("header-total-return")).toHaveClass("text-gain");
  });
});
