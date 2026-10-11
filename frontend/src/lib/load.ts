import type { Problem } from "@/lib/problem";

/** What a Server Component loaded from the API: the data, or a problem to render instead. */
export type Loaded<T> =
  | { data: T; error?: undefined; status: number }
  | { data?: undefined; error: Partial<Problem>; status: number };

interface ApiResult<T> {
  data?: T;
  error?: {
    code?: string;
    title?: string;
    detail?: string | null;
    hint?: string | null;
    request_id?: string | null;
    status?: number;
  };
  response: Response;
}

/**
 * Runs one typed API call. Never throws for an outage and never redirects on 401: the layout has
 * just checked the session, so a rejected token is a configuration problem (issuer, audience,
 * keys) and redirecting to sign-in would loop.
 */
export async function load<T>(what: string, call: () => Promise<ApiResult<T>>): Promise<Loaded<T>> {
  try {
    const { data, error, response } = await call();
    if (data !== undefined) return { data, status: response.status };
    return {
      status: response.status,
      error: {
        code: error?.code,
        title: error?.title,
        detail: error?.detail ?? undefined,
        hint:
          error?.hint ?? (response.status === 401 ? "Sign out, then sign in again." : undefined),
        request_id: error?.request_id ?? undefined,
      },
    };
  } catch (cause) {
    console.error(`[${what}] could not reach the API: ${String(cause)}`);
    return {
      status: 503,
      error: {
        title: "The analysis service is unavailable",
        detail: `We couldn’t reach it to load ${what}.`,
      },
    };
  }
}
