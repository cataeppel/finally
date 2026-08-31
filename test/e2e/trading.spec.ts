/**
 * PLAN.md §12 — "Buy shares: cash decreases, position appears, portfolio
 * updates" and "Sell shares: cash increases, position updates or disappears."
 *
 * Prices move continuously, so cash assertions use a tolerance band around the
 * expected trade value rather than an exact figure.
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import {
  apiTrade,
  ensureWatched,
  flattenPosition,
  getPortfolio,
  openApp,
  readMoney,
} from "./helpers";

/** Prices drift ~fractions of a percent per tick; 3% covers the round trip. */
const TOLERANCE = 0.03;

test.describe("Trading", () => {
  test("buy via the trade bar — cash decreases, position appears", async ({ page, request }) => {
    await ensureWatched(request, "AAPL");
    await flattenPosition(request, "AAPL");

    await openApp(page);
    const cashBefore = await readMoney(ui.cashBalance(page));

    await ui.tradeTicker(page).fill("AAPL");
    await ui.tradeQty(page).fill("5");
    await ui.tradeBuy(page).click();

    await expect(ui.tradeStatus(page)).toContainText(/BUY 5 AAPL/);

    // Position appears in the table with the right quantity.
    await expect(ui.positionRow(page, "AAPL")).toBeVisible();
    await expect(ui.positionRow(page, "AAPL")).toContainText("5");

    // Cash fell by roughly 5 * price.
    const api = await getPortfolio(request);
    const position = api.positions.find((p) => p.ticker === "AAPL");
    expect(position, "AAPL position missing from /api/portfolio").toBeDefined();
    expect(position!.quantity).toBe(5);

    await expect
      .poll(async () => readMoney(ui.cashBalance(page)), { timeout: 20_000 })
      .toBeLessThan(cashBefore);

    const cashAfter = await readMoney(ui.cashBalance(page));
    const spent = cashBefore - cashAfter;
    const expected = 5 * position!.avg_cost;
    expect(Math.abs(spent - expected)).toBeLessThan(expected * TOLERANCE);
  });

  test("portfolio total value tracks cash plus positions", async ({ page, request }) => {
    await openApp(page);

    await expect
      .poll(
        async () => {
          const api = await getPortfolio(request);
          const shown = await readMoney(ui.portfolioValue(page));
          return Math.abs(shown - api.total_value);
        },
        { timeout: 20_000 },
      )
      // Prices tick between the two reads, so allow a small drift.
      .toBeLessThan(50);
  });

  test("partial sell via the trade bar — cash increases, quantity reduces", async ({
    page,
    request,
  }) => {
    await ensureWatched(request, "MSFT");
    await flattenPosition(request, "MSFT");
    await apiTrade(request, "MSFT", "buy", 10);

    await openApp(page);
    await expect(ui.positionRow(page, "MSFT")).toBeVisible();
    const cashBefore = await readMoney(ui.cashBalance(page));

    await ui.tradeTicker(page).fill("MSFT");
    await ui.tradeQty(page).fill("4");
    await ui.tradeSell(page).click();

    await expect(ui.tradeStatus(page)).toContainText(/SELL 4 MSFT/);

    await expect
      .poll(async () => readMoney(ui.cashBalance(page)), { timeout: 20_000 })
      .toBeGreaterThan(cashBefore);

    const api = await getPortfolio(request);
    const position = api.positions.find((p) => p.ticker === "MSFT");
    expect(position, "MSFT position should still exist after a partial sell").toBeDefined();
    expect(position!.quantity).toBe(6);
    await expect(ui.positionRow(page, "MSFT")).toContainText("6");
  });

  test("selling the full position removes it from the table", async ({ page, request }) => {
    await ensureWatched(request, "GOOGL");
    await flattenPosition(request, "GOOGL");
    await apiTrade(request, "GOOGL", "buy", 2);

    await openApp(page);
    await expect(ui.positionRow(page, "GOOGL")).toBeVisible();

    await ui.tradeTicker(page).fill("GOOGL");
    await ui.tradeQty(page).fill("2");
    await ui.tradeSell(page).click();

    await expect(ui.tradeStatus(page)).toContainText(/SELL 2 GOOGL/);
    await expect(ui.positionRow(page, "GOOGL")).toHaveCount(0, { timeout: 20_000 });

    const api = await getPortfolio(request);
    expect(api.positions.map((p) => p.ticker)).not.toContain("GOOGL");
  });

  test("buying beyond available cash is rejected with an error", async ({ page, request }) => {
    await ensureWatched(request, "NVDA");
    await openApp(page);

    const cashBefore = (await getPortfolio(request)).cash;

    await ui.tradeTicker(page).fill("NVDA");
    await ui.tradeQty(page).fill("100000");
    await ui.tradeBuy(page).click();

    await expect(ui.tradeStatus(page)).toContainText(/insufficient cash/i, { timeout: 20_000 });

    // The rejected order must not have moved any money.
    const cashAfter = (await getPortfolio(request)).cash;
    expect(cashAfter).toBe(cashBefore);
  });

  test("selling more shares than held is rejected with an error", async ({ page, request }) => {
    await ensureWatched(request, "META");
    await flattenPosition(request, "META");
    await openApp(page);

    await ui.tradeTicker(page).fill("META");
    await ui.tradeQty(page).fill("50");
    await ui.tradeSell(page).click();

    await expect(ui.tradeStatus(page)).toContainText(/insufficient shares/i, { timeout: 20_000 });
    const api = await getPortfolio(request);
    expect(api.positions.map((p) => p.ticker)).not.toContain("META");
  });
});
