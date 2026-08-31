/**
 * PLAN.md §12 — "Portfolio visualization: heatmap renders with correct colors,
 * P&L chart has data points", plus the positions table contract from §10.
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import {
  apiTrade,
  ensureWatched,
  flattenPosition,
  getPortfolio,
  getSnapshots,
  openApp,
  parseRgb,
} from "./helpers";

test.describe("Portfolio visualisation", () => {
  test("heatmap renders a tile per position, coloured by P&L sign", async ({ page, request }) => {
    for (const ticker of ["AAPL", "MSFT"]) {
      await ensureWatched(request, ticker);
      await flattenPosition(request, ticker);
      await apiTrade(request, ticker, "buy", 3);
    }

    await openApp(page);

    for (const ticker of ["AAPL", "MSFT"]) {
      const tile = ui.heatmapTile(page, ticker);
      await expect(tile).toBeVisible({ timeout: 20_000 });

      // Read the P&L the tile is *currently rendering* alongside its colour, so
      // the two cannot disagree because of a price tick between reads. Small
      // tiles omit the percentage text, but the title attribute always has it.
      const [text, title, color] = await tile.evaluate((el) => [
        el.textContent ?? "",
        el.getAttribute("title") ?? "",
        getComputedStyle(el).backgroundColor,
      ]);

      const source = `${text} ${title}`;
      const match = source.match(/(-?\d+(?:\.\d+)?)%/);
      expect(match, `no P&L percentage rendered for ${ticker}: ${source}`).not.toBeNull();
      const pnlPercent = Number(match![1]);

      const [r, g] = parseRgb(color);
      if (pnlPercent > 0) {
        expect(g, `${ticker} is up ${pnlPercent}% but the tile is not green (${color})`).toBeGreaterThan(r);
      } else if (pnlPercent < 0) {
        expect(r, `${ticker} is down ${pnlPercent}% but the tile is not red (${color})`).toBeGreaterThan(g);
      } else {
        // Exactly flat renders the neutral grey — red and green stay balanced.
        expect(
          Math.abs(r - g),
          `${ticker} is flat but the tile is strongly tinted (${color})`,
        ).toBeLessThan(20);
      }
    }
  });

  test("heatmap tiles are sized by portfolio weight", async ({ page, request }) => {
    await ensureWatched(request, "AAPL");
    await ensureWatched(request, "JPM");
    await flattenPosition(request, "AAPL");
    await flattenPosition(request, "JPM");

    // Deliberately lopsided: AAPL's market value must dominate JPM's.
    await apiTrade(request, "AAPL", "buy", 20);
    await apiTrade(request, "JPM", "buy", 1);

    const api = await getPortfolio(request);
    const aaplValue = api.positions.find((p) => p.ticker === "AAPL")!.market_value;
    const jpmValue = api.positions.find((p) => p.ticker === "JPM")!.market_value;
    expect(aaplValue).toBeGreaterThan(jpmValue);

    await openApp(page);
    const big = await ui.heatmapTile(page, "AAPL").boundingBox();
    const small = await ui.heatmapTile(page, "JPM").boundingBox();
    expect(big, "AAPL heatmap tile not rendered").not.toBeNull();
    expect(small, "JPM heatmap tile not rendered").not.toBeNull();
    expect(big!.width * big!.height).toBeGreaterThan(small!.width * small!.height);
  });

  test("P&L chart plots portfolio history", async ({ page, request }) => {
    // Each trade records a snapshot, so the trades above guarantee history.
    await ensureWatched(request, "V");
    await apiTrade(request, "V", "buy", 1);
    await apiTrade(request, "V", "sell", 1);

    const snapshots = await getSnapshots(request);
    expect(snapshots.length).toBeGreaterThanOrEqual(2);
    for (const snapshot of snapshots) {
      expect(typeof snapshot.total_value).toBe("number");
      expect(Number.isNaN(Date.parse(snapshot.recorded_at))).toBe(false);
    }

    await openApp(page);
    await expect(page.getByRole("heading", { name: "Portfolio P&L" })).toBeVisible();
    await expect(ui.pnlChartEmpty(page)).toHaveCount(0, { timeout: 20_000 });

    // The chart must draw a real series, not just axes.
    const chart = ui.pnlChart(page).first();
    await expect(chart).toBeVisible({ timeout: 20_000 });
    const seriesPath = chart.locator("path.recharts-curve, path[class*='area'], path[class*='line']").first();
    await expect(seriesPath).toHaveAttribute("d", /M.*[\d.]+/, { timeout: 20_000 });
  });

  test("positions table matches the portfolio API", async ({ page, request }) => {
    await ensureWatched(request, "AMZN");
    await flattenPosition(request, "AMZN");
    await apiTrade(request, "AMZN", "buy", 7);

    await openApp(page);
    const row = ui.positionRow(page, "AMZN");
    await expect(row).toBeVisible({ timeout: 20_000 });

    const api = await getPortfolio(request);
    const position = api.positions.find((p) => p.ticker === "AMZN")!;

    await expect(row).toContainText("7");
    // Average cost is fixed at fill time, so it can be compared exactly.
    await expect(row).toContainText(position.avg_cost.toFixed(2));

    // Every column required by PLAN §10 must render a value.
    const cells = await row.locator("td").allTextContents();
    expect(cells.length, `expected 6 columns, got ${cells.length}: ${cells.join(" | ")}`)
      .toBeGreaterThanOrEqual(6);
    for (const cell of cells.slice(0, 6)) {
      expect(cell.trim(), `empty cell in positions row: ${cells.join(" | ")}`).not.toBe("");
    }
  });
});
