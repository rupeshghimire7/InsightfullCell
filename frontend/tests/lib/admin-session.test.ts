import { describe, expect, it } from "vitest";
import {
  adminCookie,
  adminCookieName,
  adminKeyMatches,
  clientAddress,
  createAdminToken,
  FAILURE_WINDOW_MS,
  FailureLimiter,
  MAX_FAILURES_OVERALL,
  MAX_FAILURES_PER_ADDRESS,
  verifyAdminToken,
} from "@/lib/admin-session";
import { parseEnv } from "@/lib/env";

const KEY = "k".repeat(40);
const base = {
  BETTER_AUTH_URL: "https://app.example.com",
  BETTER_AUTH_SECRET: "x".repeat(32),
  AUTH_DATABASE_URL: "postgresql://localhost/db",
  API_BASE_URL: "http://api:8000",
  S3_PUBLIC_ORIGIN: "https://files.example.com",
  ADMIN_DASHBOARD_KEY: KEY,
};
const env = parseEnv(base);

describe("the admin key", () => {
  it("matches only exactly, and nothing matches while the dashboard is off", () => {
    expect(adminKeyMatches(KEY, env)).toBe(true);
    expect(adminKeyMatches(`${KEY} `, env)).toBe(false);
    expect(adminKeyMatches("", env)).toBe(false);
    expect(adminKeyMatches(undefined, env)).toBe(false);
    expect(adminKeyMatches({ key: KEY }, env)).toBe(false);
    expect(adminKeyMatches("x".repeat(600), env)).toBe(false);
    const off = parseEnv({ ...base, ADMIN_DASHBOARD_KEY: "" });
    expect(adminKeyMatches(KEY, off)).toBe(false);
  });
});

describe("the admin session cookie", () => {
  it("unlocks until it expires", () => {
    const now = 1_000_000;
    const token = createAdminToken(env, now);
    expect(token).not.toContain(KEY);
    expect(verifyAdminToken(token, env, now + 29 * 60_000)).toBe(true);
    expect(verifyAdminToken(token, env, now + 30 * 60_000 + 1)).toBe(false);
  });

  it("cannot be forged, extended or reused after the key changes", () => {
    const token = createAdminToken(env);
    const [version, expires, nonce, signature] = token.split(".");
    const later = `${version}.${Number(expires) + 3_600_000}.${nonce}.${signature}`;
    expect(verifyAdminToken(later, env)).toBe(false);
    expect(verifyAdminToken(`${token}x`, env)).toBe(false);
    expect(verifyAdminToken("v1.9999999999999.abc.def", env)).toBe(false);
    expect(verifyAdminToken(undefined, env)).toBe(false);
    const rotated = parseEnv({ ...base, ADMIN_DASHBOARD_KEY: "n".repeat(40) });
    expect(verifyAdminToken(token, rotated)).toBe(false);
  });

  it("is host-only, script-proof and same-site over https", () => {
    expect(adminCookieName(env)).toBe("__Host-ic_admin");
    expect(adminCookie(env, "v", 60)).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "strict",
      path: "/",
      maxAge: 60,
    });
    const local = parseEnv({ ...base, BETTER_AUTH_URL: "http://localhost:3000" });
    expect(adminCookieName(local)).toBe("ic_admin");
    expect(adminCookie(local, "v", 60).secure).toBe(false);
  });
});

describe("FailureLimiter", () => {
  it("pauses an address after too many wrong keys, until the window ends", () => {
    let now = 0;
    const limiter = new FailureLimiter(() => now);
    for (let i = 0; i < MAX_FAILURES_PER_ADDRESS; i++) limiter.fail("1.2.3.4");
    expect(limiter.retryAfter("1.2.3.4")).toBe(FAILURE_WINDOW_MS);
    expect(limiter.retryAfter("5.6.7.8")).toBe(0);
    now = FAILURE_WINDOW_MS;
    expect(limiter.retryAfter("1.2.3.4")).toBe(0);
  });

  it("pauses everyone when guesses come from many addresses", () => {
    const limiter = new FailureLimiter(() => 0);
    for (let i = 0; i < MAX_FAILURES_OVERALL; i++) limiter.fail(`10.0.0.${i}`);
    expect(limiter.retryAfter("192.168.1.1")).toBeGreaterThan(0);
  });

  it("forgets an address's failures once it gets the key right", () => {
    const limiter = new FailureLimiter(() => 0);
    for (let i = 0; i < MAX_FAILURES_PER_ADDRESS - 1; i++) limiter.fail("a");
    limiter.succeed("a");
    limiter.fail("a");
    expect(limiter.retryAfter("a")).toBe(0);
  });
});

it("takes the client address Caddy sets", () => {
  expect(clientAddress(new Headers({ "x-forwarded-for": "203.0.113.9, 10.0.0.1" }))).toBe(
    "203.0.113.9",
  );
  expect(clientAddress(new Headers())).toBe("local");
});
