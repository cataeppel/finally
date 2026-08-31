/**
 * Global setup: block until the app under test is serving, and fail loudly
 * with a useful message if it never comes up. Also records whether the
 * database looks freshly seeded, so a dirty-volume run is reported as a
 * warning rather than showing up as confusing assertion failures.
 */
import { request } from "@playwright/test";

const BASE_URL = process.env.FINALLY_BASE_URL ?? "http://localhost:8000";
const READY_TIMEOUT_MS = 120_000;
const POLL_INTERVAL_MS = 1_000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: BASE_URL });
  const deadline = Date.now() + READY_TIMEOUT_MS;
  let lastError = "no response";

  while (Date.now() < deadline) {
    try {
      const res = await ctx.get("/api/health");
      if (res.ok()) {
        const body = await res.json();
        if (body.status === "ok") break;
        lastError = `unexpected health body: ${JSON.stringify(body)}`;
      } else {
        lastError = `health returned ${res.status()}`;
      }
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
    await sleep(POLL_INTERVAL_MS);
  }

  if (Date.now() >= deadline) {
    await ctx.dispose();
    throw new Error(
      `App at ${BASE_URL} was not healthy within ${READY_TIMEOUT_MS / 1000}s (last error: ${lastError}). ` +
        `Start it with: docker compose -f test/docker-compose.test.yml up -d --build`,
    );
  }

  // Wait for the price cache to populate so the very first spec is not racing
  // the market data source's first tick.
  const priceDeadline = Date.now() + 30_000;
  while (Date.now() < priceDeadline) {
    const res = await ctx.get("/api/watchlist");
    if (res.ok()) {
      const body = await res.json();
      const priced = body.watchlist.filter((w: { price: number | null }) => w.price !== null);
      if (priced.length > 0) break;
    }
    await sleep(POLL_INTERVAL_MS);
  }

  // Fresh-volume sanity check — a warning only, so a deliberate re-run against
  // a dirty database still executes and reports real failures.
  const portfolio = await ctx.get("/api/portfolio");
  if (portfolio.ok()) {
    const body = await portfolio.json();
    if (body.cash !== 10000 || body.positions.length !== 0) {
      console.warn(
        `[global-setup] WARNING: database is not freshly seeded ` +
          `(cash=${body.cash}, positions=${body.positions.length}). ` +
          `The fresh-start spec expects a clean volume; recreate it with ` +
          `docker compose -f test/docker-compose.test.yml down -v.`,
      );
    }
  }

  await ctx.dispose();
}
