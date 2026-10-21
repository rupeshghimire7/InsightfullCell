import { NextResponse, type NextRequest } from "next/server";
import {
  adminCookie,
  adminEnabled,
  adminKeyMatches,
  clientAddress,
  createAdminToken,
  FailureLimiter,
} from "@/lib/admin-session";
import { isTrustedOrigin } from "@/lib/backend-policy";
import { appOrigins, getEnv } from "@/lib/env";

export const dynamic = "force-dynamic";

/** A JSON body with the key is a few hundred bytes at most. */
const MAX_BODY_BYTES = 2_048;
const limiter = new FailureLimiter();

function problem(status: number, code: string, title: string, detail: string, extra?: HeadersInit) {
  return NextResponse.json(
    { type: `urn:insightfulcell:problem:${code}`, title, status, detail, code },
    {
      status,
      headers: {
        "Content-Type": "application/problem+json",
        "Cache-Control": "no-store",
        ...extra,
      },
    },
  );
}

const notFound = () => problem(404, "http-404", "Not found", "This path is not available.");

function refused(request: NextRequest): NextResponse | null {
  const env = getEnv();
  if (!adminEnabled(env)) return notFound();
  if (!isTrustedOrigin(request.method, request.headers.get("origin"), appOrigins(env))) {
    return problem(
      403,
      "forbidden-origin",
      "Request blocked",
      "Cross-site requests are not allowed.",
    );
  }
  return null;
}

/** Unlock: `{ "key": "…" }` → 204 with the session cookie, or 401 / 429. */
export async function POST(request: NextRequest) {
  const blocked = refused(request);
  if (blocked) return blocked;
  const env = getEnv();
  const address = clientAddress(request.headers);
  const wait = limiter.retryAfter(address);
  if (wait > 0) {
    const seconds = Math.ceil(wait / 1000);
    return problem(
      429,
      "too-many-attempts",
      "Too many attempts",
      `Too many wrong keys. Try again in ${Math.ceil(seconds / 60)} minutes.`,
      { "Retry-After": String(seconds) },
    );
  }
  const length = Number(request.headers.get("content-length") ?? "");
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) {
    return problem(413, "body-too-large", "Request too large", "The request body is too large.");
  }
  // Checked on the declared length first, then on what was read (a chunked body declares none).
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) {
    return problem(413, "body-too-large", "Request too large", "The request body is too large.");
  }
  let key: unknown;
  try {
    key = (JSON.parse(text) as { key?: unknown } | null)?.key;
  } catch {
    return problem(400, "bad-request", "Bad request", 'Send the key as JSON: { "key": "…" }.');
  }
  if (!adminKeyMatches(key, env)) {
    limiter.fail(address);
    console.warn("[admin] wrong dashboard key", JSON.stringify({ address }));
    return problem(401, "wrong-key", "Wrong key", "That admin key isn’t right.");
  }
  limiter.succeed(address);
  console.warn("[admin] dashboard unlocked", JSON.stringify({ address }));
  const response = new NextResponse(null, {
    status: 204,
    headers: { "Cache-Control": "no-store" },
  });
  response.cookies.set(adminCookie(env, createAdminToken(env), env.ADMIN_SESSION_MINUTES * 60));
  return response;
}

/** Lock again: clears the cookie. */
export function DELETE(request: NextRequest) {
  const blocked = refused(request);
  if (blocked) return blocked;
  const response = new NextResponse(null, {
    status: 204,
    headers: { "Cache-Control": "no-store" },
  });
  response.cookies.set(adminCookie(getEnv(), "", 0));
  return response;
}
