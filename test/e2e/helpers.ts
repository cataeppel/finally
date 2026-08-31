/**
 * Shared API + UI helpers for the FinAlly E2E suite.
 *
 * The app is single-user, so the whole suite shares one SQLite state. Specs
 * therefore assert *relative* changes (cash went down by roughly the trade
 * cost) rather than absolute values — the only exception is `fresh-start`,
 * which the Playwright project graph guarantees runs first against a clean
 * volume.
 */
import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { ui } from "./selectors";

export interface ApiPosition {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  pnl_percent: number;
}

export interface ApiPortfolio {
  positions: ApiPosition[];
  cash: number;
  total_value: number;
  unrealized_pnl: number;
}

export async function getPortfolio(request: APIRequestContext): Promise<ApiPortfolio> {
  const res = await request.get("/api/portfolio");
  expect(res.ok(), `GET /api/portfolio failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

export async function getWatchlistTickers(request: APIRequestContext): Promise<string[]> {
  const res = await request.get("/api/watchlist");
  expect(res.ok(), `GET /api/watchlist failed: ${res.status()}`).toBeTruthy();
  const body = await res.json();
  return body.watchlist.map((w: { ticker: string }) => w.ticker);
}

export async function getSnapshots(
  request: APIRequestContext,
): Promise<Array<{ total_value: number; recorded_at: string }>> {
  const res = await request.get("/api/portfolio/history");
  expect(res.ok(), `GET /api/portfolio/history failed: ${res.status()}`).toBeTruthy();
  const body = await res.json();
  return body.snapshots;
}

/** Execute a trade through the REST API (test setup, not the behaviour under test). */
export async function apiTrade(
  request: APIRequestContext,
  ticker: string,
  side: "buy" | "sell",
  quantity: number,
) {
  const res = await request.post("/api/portfolio/trade", {
    data: { ticker, side, quantity },
  });
  expect(
    res.ok(),
    `API trade ${side} ${quantity} ${ticker} failed: ${res.status()} ${await res.text()}`,
  ).toBeTruthy();
  return res.json();
}

/** Ensure `ticker` is on the watchlist (idempotent — 409 on duplicate is fine). */
export async function ensureWatched(request: APIRequestContext, ticker: string) {
  const res = await request.post("/api/watchlist", { data: { ticker } });
  expect(
    res.ok() || res.status() === 409,
    `add ${ticker} to watchlist failed: ${res.status()}`,
  ).toBeTruthy();
}

/** Ensure `ticker` is NOT on the watchlist (404 when already absent is fine). */
export async function ensureNotWatched(request: APIRequestContext, ticker: string) {
  const res = await request.delete(`/api/watchlist/${ticker}`);
  expect(
    res.ok() || res.status() === 404,
    `remove ${ticker} from watchlist failed: ${res.status()}`,
  ).toBeTruthy();
}

/** Flatten any open position in `ticker` so a spec starts from a known state. */
export async function flattenPosition(request: APIRequestContext, ticker: string) {
  const portfolio = await getPortfolio(request);
  const pos = portfolio.positions.find((p) => p.ticker === ticker);
  if (pos && pos.quantity > 0) {
    await apiTrade(request, ticker, "sell", pos.quantity);
  }
}

/** Load the app and wait for the SSE stream to report a live connection. */
export async function openApp(page: Page) {
  await page.goto("/");
  await expect(ui.connectionStatus(page)).toHaveText("Live", { timeout: 20_000 });
}

/** Parse a `$1,234.56` string into a number. */
export function parseMoney(text: string | null): number {
  expect(text, "expected a currency string, got null").not.toBeNull();
  const match = text!.match(/-?\$?[\d,]+\.\d{2}/);
  expect(match, `no currency value found in ${JSON.stringify(text)}`).not.toBeNull();
  return parseFloat(match![0].replace(/[$,]/g, ""));
}

/** Read a money value out of a locator. */
export async function readMoney(locator: Locator): Promise<number> {
  return parseMoney(await locator.textContent());
}

/**
 * Wait until a locator's text changes away from `previous`.
 * Used to prove that prices are genuinely streaming rather than static.
 */
export async function expectTextToChange(
  locator: Locator,
  previous: string,
  timeout = 15_000,
) {
  await expect(locator).not.toHaveText(previous, { timeout });
}

/** Normalise an rgb()/rgba() computed colour into [r, g, b]. */
export function parseRgb(color: string): [number, number, number] {
  const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  expect(match, `unparseable colour: ${color}`).not.toBeNull();
  return [Number(match![1]), Number(match![2]), Number(match![3])];
}
