import "server-only";
import { isAPIError } from "better-auth/api";
import { headers } from "next/headers";
import createClient from "openapi-fetch";
import { getAuth } from "@/lib/auth";
import { getEnv } from "@/lib/env";
import { isConnectionFailure, ServiceUnavailableError } from "@/lib/unavailable";
import type { paths } from "@/lib/api/schema";

/**
 * Short-lived (15 min) API token for the current session, or `null` when signed out.
 * Throws `ServiceUnavailableError` when the auth database is down: treating an outage as
 * "signed out" would bounce a signed-in user between the sign-in page and the app.
 */
export async function getApiToken(requestHeaders?: Headers): Promise<string | null> {
  const auth = getAuth();
  try {
    const result = await auth.api.getToken({ headers: requestHeaders ?? (await headers()) });
    return result.token;
  } catch (error) {
    if (isAPIError(error) && (error.statusCode === 401 || error.statusCode === 403)) {
      return null; // no session: callers treat this as unauthenticated
    }
    if (isConnectionFailure(error)) {
      console.error("[auth] token request failed: the auth database is unreachable");
      throw new ServiceUnavailableError(undefined, { cause: error });
    }
    throw error;
  }
}

/** Typed API client for Server Components. Adds the bearer token per request. */
export function serverApi() {
  return createClient<paths>({
    baseUrl: getEnv().API_BASE_URL,
    fetch: async (request) => {
      const token = await getApiToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return fetch(request, { cache: "no-store", signal: AbortSignal.timeout(15_000) });
    },
  });
}
