// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseEnv, type Env } from "@/lib/env";
import { accounts, overview } from "./admin-fixtures";

const KEY = "k".repeat(40);
const state: { env: Env; address: string } = { env: makeEnv(KEY), address: "203.0.113.7" };

function makeEnv(key: string): Env {
  return parseEnv({
    BETTER_AUTH_URL: "https://app.example.com",
    BETTER_AUTH_SECRET: "x".repeat(32),
    AUTH_DATABASE_URL: "postgresql://localhost/db",
    API_BASE_URL: "http://api:8000",
    S3_PUBLIC_ORIGIN: "https://files.example.com",
    ...(key ? { ADMIN_DASHBOARD_KEY: key } : {}),
  });
}

const loadAdminData = vi.fn();
vi.mock("@/lib/env", async (original) => ({
  ...(await original<object>()),
  getEnv: () => state.env,
}));
vi.mock("next/headers", () => ({
  headers: async () => new Headers({ "x-forwarded-for": state.address }),
}));
vi.mock("@/lib/admin-data", async (original) => ({
  ...(await original<object>()),
  loadAdminData: (...a: unknown[]) => loadAdminData(...a),
}));

/** A fresh module, so each test has its own attempt counter. */
async function actions() {
  vi.resetModules();
  return import("@/app/adminboard/actions");
}

beforeEach(() => {
  state.env = makeEnv(KEY);
  state.address = `203.0.113.${Math.floor(Math.random() * 200)}`;
  loadAdminData.mockReset().mockResolvedValue({ status: "ok", overview: overview(), accounts });
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("unlockAdmin (asked on every visit; nothing stored in the browser)", () => {
  it("answers the right key with the figures and a page-view token, never the key", async () => {
    const { unlockAdmin } = await actions();
    const result = await unlockAdmin(KEY, 7);
    expect(result).toMatchObject({ status: "ok", days: 7, accounts });
    expect(JSON.stringify(result)).not.toContain(KEY);
    expect(loadAdminData).toHaveBeenCalledWith(state.env, 7);
  });

  it("refuses a wrong key without loading anything, then pauses that address", async () => {
    const { unlockAdmin } = await actions();
    for (let i = 0; i < 5; i++) {
      expect(await unlockAdmin("wrong", 30)).toEqual({
        status: "refused",
        message: "That admin key isn’t right.",
      });
    }
    const paused = await unlockAdmin(KEY, 30);
    expect(paused).toMatchObject({ status: "refused", message: /Too many wrong keys/ });
    expect(loadAdminData).not.toHaveBeenCalled();
  });

  it("does nothing while the dashboard is off, and falls back to 30 days", async () => {
    const { unlockAdmin } = await actions();
    expect(await unlockAdmin(KEY, 999)).toMatchObject({ status: "ok", days: 30 });
    state.env = makeEnv("");
    expect(await unlockAdmin(KEY, 30)).toMatchObject({ status: "refused" });
  });
});

describe("adminPeriod", () => {
  it("needs the page's valid token; otherwise the dashboard locks", async () => {
    const { adminPeriod, unlockAdmin } = await actions();
    const unlocked = await unlockAdmin(KEY, 30);
    if (unlocked.status !== "ok") throw new Error("not unlocked");
    expect(await adminPeriod(unlocked.token, 90)).toMatchObject({ status: "ok", days: 90 });
    expect(await adminPeriod("v1.1.forged.sig", 90)).toEqual({ status: "locked" });
    state.env = makeEnv("n".repeat(40)); // the key changed: open pages lock
    expect(await adminPeriod(unlocked.token, 90)).toEqual({ status: "locked" });
  });

  it("passes on an outage of either data source", async () => {
    const { adminPeriod, unlockAdmin } = await actions();
    const unlocked = await unlockAdmin(KEY, 30);
    if (unlocked.status !== "ok") throw new Error("not unlocked");
    loadAdminData.mockResolvedValue({ status: "unavailable", part: "api" });
    expect(await adminPeriod(unlocked.token, 30)).toMatchObject({
      status: "unavailable",
      part: "api",
    });
  });
});
