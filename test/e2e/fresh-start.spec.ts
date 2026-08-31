/**
 * PLAN.md §12 — "Fresh start: default watchlist appears, $10k balance shown,
 * prices are streaming."
 *
 * This spec runs as its own Playwright project, before every other spec, so it
 * observes the seeded database untouched. It must not mutate any state.
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import { getPortfolio, getWatchlistTickers, openApp, readMoney } from "./helpers";

const DEFAULT_TICKERS = [
  "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
  "NVDA", "META", "JPM", "V", "NFLX",
];

test.describe("Fresh start", () => {
  test("seeded watchlist contains exactly the 10 default tickers", async ({ page, request }) => {
    // The API does not promise an order, so compare as a set.
    expect((await getWatchlistTickers(request)).sort()).toEqual([...DEFAULT_TICKERS].sort());

    await openApp(page);
    for (const ticker of DEFAULT_TICKERS) {
      await expect(ui.watchlistTickerCell(page, ticker)).toBeVisible();
    }
    await expect(ui.watchlist(page).locator("tbody tr")).toHaveCount(DEFAULT_TICKERS.length);
  });

  test("starting cash and portfolio value are both $10,000.00", async ({ page, request }) => {
    const portfolio = await getPortfolio(request);
    expect(portfolio.cash).toBe(10_000);
    expect(portfolio.positions).toHaveLength(0);
    expect(portfolio.total_value).toBe(10_000);

    await openApp(page);
    expect(await readMoney(ui.cashBalance(page))).toBe(10_000);
    expect(await readMoney(ui.portfolioValue(page))).toBe(10_000);
  });

  test("connection indicator reports Live", async ({ page }) => {
    await page.goto("/");
    await expect(ui.connectionStatus(page)).toHaveText("Live", { timeout: 20_000 });
  });

  test("prices stream and update in the watchlist", async ({ page }) => {
    await openApp(page);

    const price = ui.watchlistPrice(page, "AAPL");
    await expect(price).toHaveText(/\$?\d[\d,]*\.\d{2}/, { timeout: 20_000 });

    // A streaming price must actually move; the simulator ticks ~every 500ms.
    const first = (await price.textContent())!;
    await expect(price).not.toHaveText(first, { timeout: 20_000 });
  });

  test("price changes trigger the flash animation class", async ({ page }) => {
    await openApp(page);
    await expect(ui.watchlistPrice(page, "AAPL")).toHaveText(/\d/, { timeout: 20_000 });

    // Poll the row for the transient flash class applied on each tick.
    const row = ui.watchlistRow(page, "AAPL");
    await expect
      .poll(async () => (await row.getAttribute("class")) ?? "", { timeout: 20_000 })
      .toMatch(/price-flash-(up|down)/);
  });

  test("sparklines fill in progressively from the SSE stream", async ({ page }) => {
    await openApp(page);

    const sparkline = ui.watchlistSparkline(page, "AAPL");
    await expect(sparkline).toBeVisible({ timeout: 20_000 });

    // The sparkline accumulates points client-side, so its path grows over time.
    const pointCount = async () => {
      const d = await sparkline.locator("path, polyline").first().getAttribute("d")
        ?? await sparkline.locator("polyline").first().getAttribute("points");
      return (d ?? "").length;
    };

    await expect.poll(pointCount, { timeout: 20_000 }).toBeGreaterThan(0);
    const initial = await pointCount();
    await expect.poll(pointCount, { timeout: 20_000 }).toBeGreaterThan(initial);
  });

  test("header shows FinAlly branding", async ({ page }) => {
    await page.goto("/");
    await expect(ui.header(page).getByText("FinAlly")).toBeVisible();
    await expect(ui.header(page).getByText("AI Trading Workstation")).toBeVisible();
  });

  test("no positions yet — empty states are shown", async ({ page }) => {
    await openApp(page);
    await expect(ui.positionsEmpty(page)).toBeVisible();
    await expect(ui.heatmapEmpty(page)).toBeVisible();
  });
});
