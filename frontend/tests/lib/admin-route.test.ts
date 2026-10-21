// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseEnv, type Env } from "@/lib/env";

const KEY = "k".repeat(40);
const ORIGIN = "https://app.example.com";
const state: { env: Env } = { env: makeEnv(KEY) };

function makeEnv(key: string): Env {
  return parseEnv({
    BETTER_AUTH_URL: ORIGIN,
    BETTER_AUTH_SECRET: "x".repeat(32),
    AUTH_DATABASE_URL: "postgresql://localhost/db",
    API_BASE_URL: "http://api:8000",
    S3_PUBLIC_ORIGIN: "https://files.example.com",
    ADMIN_DASHBOARD_KEY: key,
  });
}

vi.mock("@/lib/env", async (original) => ({
  ...(await original<object>()),
  getEnv: () => state.env,
}));

async function route() {
  vi.resetModules(); // a fresh attempt counter per test
  return import("@/app/api/admin/session/route");
}

function post(body: string, headers: Record<string, string> = {}) {
  return new NextRequest(`${ORIGIN}/api/admin/session`, {
    method: "POST",
    body,
    headers: { origin: ORIGIN, "content-type": "application/json", ...headers },
  });
}

beforeEach(() => {
  state.env = makeEnv(KEY);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("POST /api/admin/session", () => {
  it("sets a host-only, script-proof cookie for the right key, and never echoes it", async () => {
    const { POST } = await route();
    const response = await POST(post(JSON.stringify({ key: KEY })));
    expect(response.status).toBe(204);
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/^__Host-ic_admin=v1\./);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/Secure/i);
    expect(cookie).toMatch(/SameSite=strict/i);
    expect(cookie).toMatch(/Path=\//);
    expect(cookie).not.toContain(KEY);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("refuses a wrong key, then pauses attempts from that address", async () => {
    const { POST } = await route();
    const from = { "x-forwarded-for": "203.0.113.7" };
    for (let i = 0; i < 5; i++) {
      expect((await POST(post(JSON.stringify({ key: "wrong" }), from))).status).toBe(401);
    }
    const paused = await POST(post(JSON.stringify({ key: KEY }), from));
    expect(paused.status).toBe(429);
    expect(Number(paused.headers.get("retry-after"))).toBeGreaterThan(0);
    // Another address is not held up by it.
    expect(
      (await POST(post(JSON.stringify({ key: KEY }), { "x-forwarded-for": "198.51.100.1" })))
        .status,
    ).toBe(204);
  });

  it("refuses cross-site requests, bad bodies and big bodies", async () => {
    const { POST } = await route();
    const other = await POST(
      post(JSON.stringify({ key: KEY }), { origin: "https://evil.example" }),
    );
    expect(other.status).toBe(403);
    expect(other.headers.get("set-cookie")).toBeNull();
    expect((await POST(post("not json"))).status).toBe(400);
    expect((await POST(post(JSON.stringify({ key: "x".repeat(5_000) })))).status).toBe(413);
  });

  it("does not exist while the dashboard is off", async () => {
    state.env = makeEnv("");
    const { POST, DELETE } = await route();
    expect((await POST(post(JSON.stringify({ key: KEY })))).status).toBe(404);
    const lock = new NextRequest(`${ORIGIN}/api/admin/session`, {
      method: "DELETE",
      headers: { origin: ORIGIN },
    });
    expect((await DELETE(lock)).status).toBe(404);
  });
});

describe("DELETE /api/admin/session", () => {
  it("clears the cookie", async () => {
    const { DELETE } = await route();
    const response = await DELETE(
      new NextRequest(`${ORIGIN}/api/admin/session`, {
        method: "DELETE",
        headers: { origin: ORIGIN },
      }),
    );
    expect(response.status).toBe(204);
    expect(response.headers.get("set-cookie")).toMatch(/__Host-ic_admin=;.*Max-Age=0/i);
  });
});
