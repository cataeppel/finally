/**
 * Central locator factory for the FinAlly UI.
 *
 * Every spec addresses the DOM through this module so that the coupling
 * between tests and markup lives in exactly one file. Each locator prefers a
 * stable `data-testid` hook and falls back to the structural selector that the
 * current frontend build exposes, so the suite works both before and after the
 * frontend engineer adds the hooks.
 */
import type { Locator, Page } from "@playwright/test";

/**
 * `[data-testid=id]` if present in the DOM, otherwise the structural fallback.
 *
 * `.first()` keeps the union unambiguous: once a testid lands, the structural
 * fallback usually still matches a nested element, and a bare `.or()` would
 * fail Playwright's strict-mode check. Absence assertions still work, because
 * `.first()` resolves to a count of 0 when nothing matches.
 */
function preferTestId(page: Page, id: string, fallback: Locator): Locator {
  return page.locator(`[data-testid="${id}"]`).or(fallback).first();
}

export const ui = {
  // ---------------------------------------------------------------- header
  header: (page: Page) => page.locator("header"),

  portfolioValue: (page: Page) =>
    preferTestId(
      page,
      "header-portfolio-value",
      page.locator("header").getByText(/\$[\d,]+\.\d{2}/).nth(0),
    ),

  cashBalance: (page: Page) =>
    preferTestId(
      page,
      "header-cash",
      page.locator("header").getByText(/\$[\d,]+\.\d{2}/).nth(1),
    ),

  connectionStatus: (page: Page) =>
    preferTestId(
      page,
      "connection-status",
      page.locator("header").getByText(/^(Live|Connecting\.\.\.|Disconnected)$/),
    ),

  // ------------------------------------------------------------- watchlist
  watchlist: (page: Page) =>
    preferTestId(page, "watchlist", page.locator("table").first()),

  watchlistRow: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `watchlist-row-${ticker}`,
      ui.watchlist(page).locator("tbody tr").filter({
        has: page.getByText(ticker, { exact: true }),
      }),
    ),

  watchlistTickerCell: (page: Page, ticker: string) =>
    ui.watchlist(page).getByText(ticker, { exact: true }),

  watchlistPrice: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `watchlist-price-${ticker}`,
      ui.watchlistRow(page, ticker).locator("td").nth(1),
    ),

  watchlistSparkline: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `watchlist-sparkline-${ticker}`,
      ui.watchlistRow(page, ticker).locator("svg"),
    ),

  watchlistRemove: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `watchlist-remove-${ticker}`,
      ui.watchlistRow(page, ticker).locator("button", { hasText: "x" }),
    ),

  watchlistAddInput: (page: Page) =>
    preferTestId(page, "watchlist-add-input", page.getByPlaceholder("Add ticker")),

  // ------------------------------------------------------------- trade bar
  tradeTicker: (page: Page) =>
    preferTestId(page, "trade-ticker", page.getByPlaceholder("Ticker")),

  tradeQty: (page: Page) =>
    preferTestId(page, "trade-qty", page.getByPlaceholder("Qty")),

  tradeBuy: (page: Page) =>
    preferTestId(page, "trade-buy", page.getByRole("button", { name: "BUY" })),

  tradeSell: (page: Page) =>
    preferTestId(page, "trade-sell", page.getByRole("button", { name: "SELL" })),

  tradeStatus: (page: Page) =>
    preferTestId(
      page,
      "trade-status",
      page.locator("div").filter({ hasText: /^(BUY|SELL) [\d.]+ [A-Z]+ @/ }).last(),
    ),

  // ------------------------------------------------------------- positions
  positionsTable: (page: Page) =>
    preferTestId(
      page,
      "positions-table",
      page.locator("table").filter({ hasText: "Avg Cost" }),
    ),

  positionRow: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `position-row-${ticker}`,
      ui.positionsTable(page).locator("tbody tr").filter({
        has: page.getByText(ticker, { exact: true }),
      }),
    ),

  positionsEmpty: (page: Page) =>
    preferTestId(page, "positions-empty", page.getByText("No open positions")),

  // --------------------------------------------------------------- heatmap
  heatmap: (page: Page) =>
    preferTestId(
      page,
      "heatmap",
      page.locator("div").filter({ has: page.getByRole("heading", { name: "Portfolio Heatmap" }) }).last(),
    ),

  heatmapTile: (page: Page, ticker: string) =>
    preferTestId(
      page,
      `heatmap-tile-${ticker}`,
      ui
        .heatmap(page)
        .locator("div")
        .filter({ has: page.getByText(ticker, { exact: true }) })
        .last(),
    ),

  heatmapEmpty: (page: Page) =>
    preferTestId(page, "heatmap-empty", page.getByText("No positions to display")),

  // ------------------------------------------------------------- pnl chart
  pnlChart: (page: Page) =>
    preferTestId(
      page,
      "pnl-chart",
      page
        .locator("div")
        .filter({ has: page.getByRole("heading", { name: "Portfolio P&L" }) })
        .last()
        .locator("svg"),
    ),

  pnlChartEmpty: (page: Page) =>
    preferTestId(page, "pnl-chart-empty", page.getByText("Waiting for portfolio history")),

  // ----------------------------------------------------------- price chart
  priceChart: (page: Page) =>
    preferTestId(
      page,
      "price-chart",
      page
        .locator("div")
        .filter({ has: page.getByRole("heading", { name: /Price|Chart/ }) })
        .last(),
    ),

  // ------------------------------------------------------------------ chat
  chatPanel: (page: Page) =>
    preferTestId(
      page,
      "chat-panel",
      page.locator("div").filter({ has: page.getByRole("heading", { name: "AI Assistant" }) }).last(),
    ),

  chatInput: (page: Page) =>
    preferTestId(page, "chat-input", page.getByPlaceholder("Ask about your portfolio...")),

  chatSend: (page: Page) =>
    preferTestId(page, "chat-send", page.getByRole("button", { name: "Send" })),

  chatLoading: (page: Page) =>
    preferTestId(page, "chat-loading", page.getByText("Thinking...")),
};

/** CSS classes the watchlist applies for the price-flash animation. */
export const FLASH_CLASSES = ["price-flash-up", "price-flash-down"];
