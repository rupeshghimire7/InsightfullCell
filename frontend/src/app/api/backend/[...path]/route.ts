import { NextResponse, type NextRequest } from "next/server";
import {
  declaredBodyTooLarge,
  FORWARDED_REQUEST_HEADERS,
  FORWARDED_RESPONSE_HEADERS,
  isTrustedOrigin,
  MAX_BODY_BYTES,
  resolveBackendPath,
} from "@/lib/backend-policy";
import { getApiToken } from "@/lib/backend";
import { appOrigins, getEnv } from "@/lib/env";
import { ServiceUnavailableError } from "@/lib/unavailable";

export const dynamic = "force-dynamic";

const PROBLEM = "application/problem+json";

function problem(status: number, code: string, title: string, detail: string) {
  return NextResponse.json(
    { type: `urn:insightfulcell:problem:${code}`, title, status, detail, code },
    { status, headers: { "Content-Type": PROBLEM, "Cache-Control": "no-store" } },
  );
}

/**
 * Backend-for-frontend: the browser never holds the API token. It calls this route with its
 * session cookie; we mint a 15-minute token and forward an allow-listed request to the API.
 */
async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const env = getEnv();
  const { path: segments } = await context.params;

  const apiPath = resolveBackendPath(segments);
  if (!apiPath) return problem(404, "http-404", "Not found", "This path is not available.");

  if (!isTrustedOrigin(request.method, request.headers.get("origin"), appOrigins(env))) {
    return problem(
      403,
      "forbidden-origin",
      "Request blocked",
      "Cross-site requests are not allowed.",
    );
  }

  let token: string | null;
  try {
    token = await getApiToken(request.headers);
  } catch (error) {
    if (!(error instanceof ServiceUnavailableError)) throw error;
    return problem(
      503,
      "auth-unavailable",
      "Sign-in is unavailable",
      "We couldn't check your session. Try again in a moment.",
    );
  }
  if (!token) {
    return problem(401, "unauthenticated", "Authentication required", "Sign in to continue.");
  }

  const outgoing = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) outgoing.set(name, value);
  }
  const requestId = request.headers.get("x-request-id");
  if (requestId) outgoing.set("x-request-id", requestId);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  // Checked on the declared length first, then on what was read (a chunked body declares none).
  const body =
    hasBody && !declaredBodyTooLarge(request.headers.get("content-length"))
      ? await request.text()
      : undefined;
  if (hasBody && (body === undefined || new TextEncoder().encode(body).length > MAX_BODY_BYTES)) {
    return problem(
      413,
      "body-too-large",
      "Request too large",
      "The request body is larger than this service accepts.",
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${env.API_BASE_URL}/${apiPath}${request.nextUrl.search}`, {
      method: request.method,
      headers: outgoing,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return problem(
      503,
      "api-unavailable",
      "The service is unavailable",
      "We couldn't reach the analysis service. Try again in a moment.",
    );
  }

  const headers = new Headers({ "Cache-Control": "no-store" });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers,
  });
}

export { forward as GET, forward as POST, forward as DELETE };
