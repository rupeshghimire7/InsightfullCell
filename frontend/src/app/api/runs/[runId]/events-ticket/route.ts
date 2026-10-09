import { NextResponse, type NextRequest } from "next/server";
import { isTrustedOrigin } from "@/lib/backend-policy";
import { getApiToken } from "@/lib/backend";
import { getAuth } from "@/lib/auth";
import { apiPublicUrl, getEnv } from "@/lib/env";
import { identityFromApiToken, TICKET_SECONDS, ticketClaims } from "@/lib/events-ticket";
import { isUuid } from "@/lib/ids";
import { ServiceUnavailableError } from "@/lib/unavailable";

export const dynamic = "force-dynamic";

function problem(status: number, code: string, title: string, detail: string) {
  return NextResponse.json(
    { type: `urn:insightfulcell:problem:${code}`, title, status, detail, code },
    {
      status,
      headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store" },
    },
  );
}

/**
 * Mints a 5-minute ticket for one run's progress stream. The session cookie proves who is
 * asking; the ticket repeats the claims of their API token (organisation, role) and adds the run.
 * Whether the run exists in their organisation is checked by the API when the stream opens.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  const env = getEnv();
  const { runId } = await context.params;
  if (!isUuid(runId)) {
    return problem(404, "run-not-found", "Run not found", "No run with this id exists.");
  }
  if (
    !isTrustedOrigin("POST", request.headers.get("origin"), new URL(env.BETTER_AUTH_URL).origin)
  ) {
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
  const identity = token ? identityFromApiToken(token) : null;
  if (!identity) {
    return problem(401, "unauthenticated", "Authentication required", "Sign in to continue.");
  }

  const now = Math.floor(Date.now() / 1000);
  const { token: ticket } = await getAuth().api.signJWT({
    body: { payload: ticketClaims(identity, runId, env.AUTH_EVENTS_AUDIENCE, now) },
  });
  return NextResponse.json(
    {
      ticket,
      url: `${apiPublicUrl(env)}/v1/runs/${runId}/events`,
      expires_at: new Date((now + TICKET_SECONDS) * 1000).toISOString(),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
