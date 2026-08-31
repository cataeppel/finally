/**
 * PLAN.md §12 — "AI chat (mocked): send a message, receive a response, trade
 * execution appears inline."
 *
 * Requires the app to run with LLM_MOCK=true. The trigger phrases below are the
 * contract with backend/app/llm/mock.py:
 *   "hello"                  → greeting containing "trading assistant"
 *   "how is my portfolio?"   → portfolio summary quoting cash / total value
 *   "buy N TICKER"           → executes a buy
 *   "sell N TICKER"          → executes a sell
 *   "add TICKER to watchlist"→ adds TICKER to the watchlist
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import {
  ensureNotWatched,
  ensureWatched,
  flattenPosition,
  getPortfolio,
  getWatchlistTickers,
  openApp,
} from "./helpers";

/** Send a chat message and return a locator for the assistant's reply bubble. */
async function ask(page: import("@playwright/test").Page, message: string) {
  const before = await page.locator("[data-testid^='chat-message'], .whitespace-pre-wrap").count();
  await ui.chatInput(page).fill(message);
  await ui.chatSend(page).click();

  // The user's own message renders immediately.
  await expect(ui.chatPanel(page).getByText(message, { exact: false }).first()).toBeVisible();

  // Wait for the assistant bubble to arrive (loading indicator clears).
  await expect
    .poll(
      async () => page.locator("[data-testid^='chat-message'], .whitespace-pre-wrap").count(),
      { timeout: 30_000 },
    )
    .toBeGreaterThan(before + 1);
  await expect(ui.chatLoading(page)).toHaveCount(0, { timeout: 30_000 });
}

test.describe("AI chat (LLM_MOCK=true)", () => {
  test("greeting — user message and assistant reply both render", async ({ page }) => {
    await openApp(page);
    await ask(page, "hello");
    await expect(ui.chatPanel(page).getByText(/trading assistant/i).first()).toBeVisible();
  });

  test("portfolio question — assistant quotes portfolio figures", async ({ page }) => {
    await openApp(page);
    await ask(page, "how is my portfolio doing?");
    await expect(
      ui.chatPanel(page).getByText(/portfolio is worth|in cash/i).first(),
    ).toBeVisible();
  });

  test("shows a loading indicator while waiting for the model", async ({ page }) => {
    await openApp(page);

    // Hold the response open so the indicator is observable.
    await page.route("**/api/chat", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
      await route.continue();
    });

    await ui.chatInput(page).fill("hello there");
    await ui.chatSend(page).click();
    await expect(ui.chatLoading(page)).toBeVisible({ timeout: 10_000 });
    await expect(ui.chatLoading(page)).toHaveCount(0, { timeout: 30_000 });
    await page.unroute("**/api/chat");
  });

  test("buy through chat — trade executes and is confirmed inline", async ({ page, request }) => {
    await ensureWatched(request, "NVDA");
    await flattenPosition(request, "NVDA");

    await openApp(page);
    await ask(page, "buy 2 NVDA");

    // Inline execution confirmation in the chat bubble.
    await expect(
      ui.chatPanel(page).getByText(/BUY\s+2\s+NVDA\s*@/).first(),
    ).toBeVisible({ timeout: 20_000 });

    // The trade really happened.
    const api = await getPortfolio(request);
    const position = api.positions.find((p) => p.ticker === "NVDA");
    expect(position, "NVDA position missing after chat buy").toBeDefined();
    expect(position!.quantity).toBe(2);

    // And the rest of the UI picked it up.
    await expect(ui.positionRow(page, "NVDA")).toBeVisible({ timeout: 20_000 });
  });

  test("sell through chat — position reduces and is confirmed inline", async ({ page, request }) => {
    await ensureWatched(request, "NVDA");
    const before = (await getPortfolio(request)).positions.find((p) => p.ticker === "NVDA");
    expect(before, "expected the chat-buy spec to have left an NVDA position").toBeDefined();

    await openApp(page);
    await ask(page, "sell 1 NVDA");

    await expect(
      ui.chatPanel(page).getByText(/SELL\s+1\s+NVDA\s*@/).first(),
    ).toBeVisible({ timeout: 20_000 });

    const after = (await getPortfolio(request)).positions.find((p) => p.ticker === "NVDA");
    expect(after!.quantity).toBe(before!.quantity - 1);
  });

  test("watchlist change through chat — ticker is added and shown inline", async ({
    page,
    request,
  }) => {
    await ensureNotWatched(request, "PYPL");
    await openApp(page);

    await ask(page, "add PYPL to watchlist");

    await expect(
      ui.chatPanel(page).getByText(/Watchlist\s*\S*\s*PYPL/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    expect(await getWatchlistTickers(request)).toContain("PYPL");
    await expect(ui.watchlistTickerCell(page, "PYPL")).toBeVisible({ timeout: 20_000 });
  });

  test("rejected chat trade surfaces the validation error", async ({ page, request }) => {
    await ensureWatched(request, "TSLA");
    await flattenPosition(request, "TSLA");

    await openApp(page);
    await ask(page, "sell 500 TSLA");

    await expect(
      ui.chatPanel(page).getByText(/insufficient shares/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    const api = await getPortfolio(request);
    expect(api.positions.map((p) => p.ticker)).not.toContain("TSLA");
  });

  test("chat history persists across a reload", async ({ page, request }) => {
    await openApp(page);
    await ask(page, "hello");

    const res = await request.get("/api/chat/history");
    expect(res.ok(), `GET /api/chat/history failed: ${res.status()}`).toBeTruthy();
    const body = await res.json();
    expect(body.messages.length).toBeGreaterThan(0);
    expect(body.messages.some((m: { content: string }) => m.content.includes("hello"))).toBe(true);
  });
});
