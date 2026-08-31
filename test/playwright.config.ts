import { defineConfig } from "@playwright/test";

const baseURL = process.env.FINALLY_BASE_URL ?? "http://localhost:8000";

/**
 * FinAlly E2E configuration.
 *
 * The app is single-user: one SQLite database backs every test, so the suite
 * must run serially (`workers: 1`) and the specs that assert pristine seed
 * state must run before anything mutates it. That ordering is enforced by the
 * project graph below — `fresh-start` runs first, and everything else depends
 * on it. Run against a container started from a clean volume.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./global-setup.ts",
  use: {
    baseURL,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
  },
  projects: [
    {
      // Asserts the pristine seeded state; must observe the DB before any
      // other spec writes to it.
      name: "fresh-start",
      testMatch: /fresh-start\.spec\.ts/,
      use: { browserName: "chromium" },
    },
    {
      name: "app",
      testIgnore: /fresh-start\.spec\.ts/,
      dependencies: ["fresh-start"],
      use: { browserName: "chromium" },
    },
  ],
});
