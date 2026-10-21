import "server-only";
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { Env } from "@/lib/env";

/**
 * The admin dashboard's lock (`/dashboard`). The key (`ADMIN_DASHBOARD_KEY`) stays on the server:
 * the browser sends it once, and gets back a short-lived cookie signed with a secret derived from
 * the key, so changing the key locks every open dashboard. The cookie is `HttpOnly` (no script can
 * read it), `SameSite=Strict`, and over https `__Host-` prefixed: host-only, so no other site on the
 * same domain can set or overwrite it.
 */

const TOKEN_VERSION = "v1";
/** Failed attempts per address, and from everyone, before attempts pause. */
export const MAX_FAILURES_PER_ADDRESS = 5;
export const MAX_FAILURES_OVERALL = 30;
export const FAILURE_WINDOW_MS = 15 * 60 * 1000;

export function adminEnabled(env: Env): boolean {
  return Boolean(env.ADMIN_DASHBOARD_KEY);
}

export function adminCookieName(env: Env): string {
  return env.BETTER_AUTH_URL.startsWith("https://") ? "__Host-ic_admin" : "ic_admin";
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

/** Constant-time comparison of digests (so neither the content nor the length leaks). */
export function adminKeyMatches(supplied: unknown, env: Env): boolean {
  const key = env.ADMIN_DASHBOARD_KEY;
  if (!key || typeof supplied !== "string" || supplied.length === 0 || supplied.length > 512) {
    return false;
  }
  return timingSafeEqual(digest(supplied), digest(key));
}

function signingKey(env: Env): Buffer {
  // Derived, so the cookie never carries anything the key could be recovered from.
  return createHmac("sha256", env.ADMIN_DASHBOARD_KEY ?? "")
    .update("insightfulcell admin session")
    .digest();
}

function sign(payload: string, env: Env): string {
  return createHmac("sha256", signingKey(env)).update(payload).digest("base64url");
}

/** A cookie value that unlocks the dashboard until `ADMIN_SESSION_MINUTES` from `now`. */
export function createAdminToken(env: Env, now: number = Date.now()): string {
  const expires = now + env.ADMIN_SESSION_MINUTES * 60_000;
  const payload = `${TOKEN_VERSION}.${expires}.${randomBytes(16).toString("base64url")}`;
  return `${payload}.${sign(payload, env)}`;
}

export function verifyAdminToken(
  token: string | undefined,
  env: Env,
  now: number = Date.now(),
): boolean {
  if (!adminEnabled(env) || !token || token.length > 200) return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== TOKEN_VERSION) return false;
  const payload = parts.slice(0, 3).join(".");
  const expected = Buffer.from(sign(payload, env));
  const actual = Buffer.from(parts[3] ?? "");
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) return false;
  const expires = Number(parts[1]);
  return Number.isFinite(expires) && expires > now;
}

export function adminCookie(env: Env, value: string, maxAgeSeconds: number) {
  return {
    name: adminCookieName(env),
    value,
    httpOnly: true,
    secure: env.BETTER_AUTH_URL.startsWith("https://"),
    sameSite: "strict" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

interface Window {
  count: number;
  resetAt: number;
}

/**
 * Wrong keys are counted per address and in total; past either limit, attempts pause until the
 * window ends (even a right key, so guessing cannot continue between the limits). In memory: one
 * web server per deployment, and a restart only resets the count.
 */
export class FailureLimiter {
  private readonly byAddress = new Map<string, Window>();
  private overall: Window = { count: 0, resetAt: 0 };

  constructor(private readonly clock: () => number = Date.now) {}

  /** Milliseconds until attempts are allowed again, or 0 when they are. */
  retryAfter(address: string): number {
    const now = this.clock();
    this.prune(now);
    const own = this.byAddress.get(address);
    const waits = [
      own && own.count >= MAX_FAILURES_PER_ADDRESS ? own.resetAt - now : 0,
      this.overall.count >= MAX_FAILURES_OVERALL ? this.overall.resetAt - now : 0,
    ];
    return Math.max(0, ...waits);
  }

  fail(address: string): void {
    const now = this.clock();
    this.prune(now);
    const own = this.byAddress.get(address) ?? { count: 0, resetAt: now + FAILURE_WINDOW_MS };
    own.count += 1;
    this.byAddress.set(address, own);
    if (this.overall.resetAt <= now) this.overall = { count: 0, resetAt: now + FAILURE_WINDOW_MS };
    this.overall.count += 1;
  }

  succeed(address: string): void {
    this.byAddress.delete(address);
  }

  private prune(now: number): void {
    for (const [address, window] of this.byAddress) {
      if (window.resetAt <= now) this.byAddress.delete(address);
    }
    if (this.overall.resetAt <= now) this.overall = { count: 0, resetAt: 0 };
  }
}

/** The client address Caddy put in `X-Forwarded-For` (it overwrites any the client sent). */
export function clientAddress(headers: Headers): string {
  return headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "local";
}
