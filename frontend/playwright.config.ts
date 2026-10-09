import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests run against a fully started stack (database, API, object storage, web app);
 * `scripts/e2e.sh` starts it and passes the addresses in. They are not part of `pnpm test`.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
