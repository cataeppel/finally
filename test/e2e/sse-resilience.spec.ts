/**
 * PLAN.md §12 — "SSE resilience: disconnect and verify reconnection."
 */
import { test, expect } from "@playwright/test";
import { ui } from "./selectors";
import { openApp } from "./helpers";

test.describe("SSE resilience", () => {
  test("a dropped stream is re-established and prices resume", async ({ page }) => {
    // Serve the first connection as a stream that ends immediately, which is
    // what the client sees when the server drops an established SSE stream.
    // Chromium's offline emulation cannot be used here: it does not tear down
    // an already-open EventSource, so the client never observes the drop.
    let served = 0;
    await page.route("**/api/stream/prices", async (route) => {
      served += 1;
      if (served === 1) {
        await route.fulfill({
          status: 200,
          headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
          body: "retry: 500\n\ndata: {}\n\n",
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");

    // The client must dial again by itself.
    await expect.poll(() => served, { timeout: 30_000 }).toBeGreaterThan(1);
    await expect(ui.connectionStatus(page)).toHaveText("Live", { timeout: 45_000 });

    // Prices must resume moving after the reconnect, not just show a stale value.
    const price = ui.watchlistPrice(page, "AAPL");
    await expect(price).toHaveText(/\d/, { timeout: 30_000 });
    const afterReconnect = (await price.textContent())!;
    await expect(price).not.toHaveText(afterReconnect, { timeout: 30_000 });
    await page.unroute("**/api/stream/prices");
  });

  test("stream endpoint failure is reported, then recovered from", async ({ page }) => {
    let fail = true;
    await page.route("**/api/stream/prices", async (route) => {
      if (fail) {
        await route.abort("connectionfailed");
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await expect(ui.connectionStatus(page)).not.toHaveText("Live", { timeout: 30_000 });

    // Allow the stream through; the client's retry loop should recover.
    fail = false;
    await expect(ui.connectionStatus(page)).toHaveText("Live", { timeout: 45_000 });
    await expect(ui.watchlistPrice(page, "AAPL")).toHaveText(/\d/, { timeout: 30_000 });
    await page.unroute("**/api/stream/prices");
  });

  test("the SSE endpoint pushes well-formed price events", async ({ page }) => {
    await page.goto("/");

    // Open an independent EventSource and capture the first payload. The stream
    // never ends, so it has to be read from the browser rather than fetched.
    const payload = await page.evaluate<Record<string, Record<string, unknown>>>(() => {
      return new Promise((resolve, reject) => {
        const es = new EventSource("/api/stream/prices");
        const timer = setTimeout(() => {
          es.close();
          reject(new Error("no SSE event received within 20s"));
        }, 20_000);
        es.onmessage = (event) => {
          clearTimeout(timer);
          es.close();
          resolve(JSON.parse(event.data));
        };
        es.onerror = () => {
          clearTimeout(timer);
          es.close();
          reject(new Error("EventSource errored before delivering an event"));
        };
      });
    });

    const entries = Object.values(payload);
    expect(entries.length, "SSE payload contained no tickers").toBeGreaterThan(0);
    const first = entries[0];
    expect(first).toMatchObject({
      ticker: expect.any(String),
      price: expect.any(Number),
      previous_price: expect.any(Number),
    });
    expect(first.timestamp).toBeDefined();
    expect(["up", "down", "flat"]).toContain(first.direction);
  });
});
