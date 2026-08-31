/**
 * PLAN.md §12 — "Add and remove a ticker from the watchlist."
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import {
  ensureNotWatched,
  ensureWatched,
  getWatchlistTickers,
  openApp,
} from "./helpers";

const NEW_TICKER = "PYPL";
const REMOVABLE_TICKER = "NFLX";

test.describe("Watchlist management", () => {
  test("add a ticker — appears in the UI, is persisted, and starts streaming", async ({
    page,
    request,
  }) => {
    await ensureNotWatched(request, NEW_TICKER);
    await openApp(page);
    await expect(ui.watchlistTickerCell(page, NEW_TICKER)).toHaveCount(0);

    await ui.watchlistAddInput(page).fill(NEW_TICKER);
    await ui.watchlistAddInput(page).press("Enter");

    await expect(ui.watchlistTickerCell(page, NEW_TICKER)).toBeVisible();
    expect(await getWatchlistTickers(request)).toContain(NEW_TICKER);

    // A newly watched ticker must join the price stream.
    await expect(ui.watchlistPrice(page, NEW_TICKER)).toHaveText(/\$?\d[\d,]*\.\d{2}/, {
      timeout: 20_000,
    });

    // And survive a reload (it is stored in SQLite, not component state).
    await page.reload();
    await expect(ui.watchlistTickerCell(page, NEW_TICKER)).toBeVisible();
  });

  test("remove a ticker — disappears from the UI and from the API", async ({
    page,
    request,
  }) => {
    await ensureWatched(request, REMOVABLE_TICKER);
    await openApp(page);
    await expect(ui.watchlistTickerCell(page, REMOVABLE_TICKER)).toBeVisible();

    await ui.watchlistRemove(page, REMOVABLE_TICKER).click();

    await expect(ui.watchlistTickerCell(page, REMOVABLE_TICKER)).toHaveCount(0);
    expect(await getWatchlistTickers(request)).not.toContain(REMOVABLE_TICKER);

    await page.reload();
    await expect(ui.connectionStatus(page)).toHaveText("Live", { timeout: 20_000 });
    await expect(ui.watchlistTickerCell(page, REMOVABLE_TICKER)).toHaveCount(0);
  });

  test("clicking a watchlist row selects that ticker in the main chart", async ({
    page,
    request,
  }) => {
    await ensureWatched(request, "TSLA");
    await openApp(page);

    await ui.watchlistRow(page, "TSLA").click();
    await expect(ui.priceChart(page).getByText("TSLA", { exact: false }).first()).toBeVisible();
  });

  test("adding a duplicate ticker does not create a second row", async ({ page, request }) => {
    await ensureWatched(request, "AAPL");
    await openApp(page);

    await ui.watchlistAddInput(page).fill("AAPL");
    await ui.watchlistAddInput(page).press("Enter");

    await expect(ui.watchlistRow(page, "AAPL")).toHaveCount(1);
    const tickers = await getWatchlistTickers(request);
    expect(tickers.filter((t) => t === "AAPL")).toHaveLength(1);
  });
});
