import { NextResponse, type NextRequest } from "next/server";
import { getEnv } from "@/lib/env";
import { isUuid } from "@/lib/ids";

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
 * Relays one run's progress stream from an API that only this computer reaches, for pages opened
 * from another address (`lib/relay.ts`; the events ticket points here then). The run-scoped
 * ticket in the Authorization header is what authorises the stream: the API checks it.
 */
export async function GET(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  if (!isUuid(runId)) {
    return problem(404, "run-not-found", "Run not found", "No run with this id exists.");
  }
  const outgoing = new Headers({ Accept: "text/event-stream" });
  for (const name of ["authorization", "last-event-id", "x-request-id"]) {
    const value = request.headers.get(name);
    if (value) outgoing.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${getEnv().API_BASE_URL}/v1/runs/${runId}/events`, {
      headers: outgoing,
      cache: "no-store",
      // Closing the page closes the API's stream too.
      signal: request.signal,
    });
  } catch {
    return problem(
      503,
      "api-unavailable",
      "The service is unavailable",
      "We couldn't reach the analysis service. Try again in a moment.",
    );
  }

  const headers = new Headers({
    // no-transform: compression would buffer the stream until it ends.
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
  });
  const type = upstream.headers.get("content-type");
  if (type) headers.set("Content-Type", type);
  return new Response(upstream.body, { status: upstream.status, headers });
}
