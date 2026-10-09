import { getSessionCookie } from "better-auth/cookies";
import { NextResponse, type NextRequest } from "next/server";
import { buildCsp, generateNonce } from "@/lib/csp";
import { getEnv } from "@/lib/env";

/**
 * Runs before every page request (Next.js 16 `proxy`, formerly middleware):
 * 1. optimistic sign-in redirect: a missing session cookie never reaches a page (the real check
 *    happens in the layout and the API; a cookie's presence proves nothing);
 * 2. a per-request nonce for the Content-Security-Policy.
 */
export function proxy(request: NextRequest) {
  const env = getEnv();
  const { pathname } = request.nextUrl;

  const isPublic = pathname === "/sign-in" || pathname.startsWith("/api/");
  if (!isPublic && !getSessionCookie(request)) {
    return NextResponse.redirect(new URL("/sign-in", request.url));
  }

  const nonce = generateNonce();
  const csp = buildCsp({
    nonce,
    storageOrigin: env.S3_PUBLIC_ORIGIN,
    development: env.NODE_ENV !== "production",
    upgradeInsecureRequests: env.BETTER_AUTH_URL.startsWith("https://"),
  });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("content-security-policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};
