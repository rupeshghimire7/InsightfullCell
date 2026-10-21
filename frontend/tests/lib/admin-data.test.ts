import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadAdminData } from "@/lib/admin-data";
import { parseEnv } from "@/lib/env";
import { overview } from "./admin-fixtures";

const query = vi.fn();
vi.mock("@/lib/auth", () => ({ getAuthPool: () => ({ query }) }));
const fetchMock = vi.fn();

const env = parseEnv({
  BETTER_AUTH_URL: "https://app.example.com",
  BETTER_AUTH_SECRET: "x".repeat(32),
  AUTH_DATABASE_URL: "postgresql://localhost/db",
  API_BASE_URL: "http://api:8000",
  S3_PUBLIC_ORIGIN: "https://files.example.com",
  ADMIN_DASHBOARD_KEY: "k".repeat(40),
});

const row = {
  id: "u1",
  name: "Ada",
  email: "ada@example.com",
  emailVerified: false,
  createdAt: new Date("2026-10-20T00:00:00Z"),
  providers: null,
  workspace: null,
  lastActiveAt: null,
  liveSessions: "0",
};

beforeEach(() => {
  query.mockReset().mockResolvedValue({ rows: [row] });
  fetchMock.mockReset().mockResolvedValue(Response.json(overview()));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("loadAdminData", () => {
  it("asks the API over the private address with the key, and reads accounts", async () => {
    const data = await loadAdminData(env, 7);
    expect(data.status).toBe("ok");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api:8000/v1/admin/overview?days=7");
    expect((init.headers as Record<string, string>)["X-Admin-Key"]).toBe("k".repeat(40));
    expect(init.cache).toBe("no-store");
    if (data.status !== "ok") throw new Error("expected data");
    expect(data.accounts).toEqual([
      {
        id: "u1",
        name: "Ada",
        email: "ada@example.com",
        emailVerified: false,
        createdAt: "2026-10-20T00:00:00.000Z",
        providers: [],
        workspace: null,
        lastActiveAt: null,
        liveSessions: 0,
      },
    ]);
    // Never a token, a password hash or a session secret.
    expect(String(query.mock.calls[0]?.[0])).not.toMatch(/password|token|"accessToken"/i);
  });

  it("says which part is unavailable, never throws", async () => {
    fetchMock.mockResolvedValue(new Response("", { status: 404 }));
    expect(await loadAdminData(env, 30)).toEqual({ status: "unavailable", part: "api" });
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect(await loadAdminData(env, 30)).toEqual({ status: "unavailable", part: "api" });
    fetchMock.mockResolvedValue(Response.json(overview()));
    query.mockRejectedValue(new Error("connection refused"));
    expect(await loadAdminData(env, 30)).toEqual({ status: "unavailable", part: "auth" });
  });
});
