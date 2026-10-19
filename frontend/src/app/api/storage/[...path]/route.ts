import { NextResponse, type NextRequest } from "next/server";
import { isTrustedOrigin } from "@/lib/backend-policy";
import { appOrigins, getEnv } from "@/lib/env";
import { isLoopbackHost, STORAGE_RELAY_PATH } from "@/lib/relay";

export const dynamic = "force-dynamic";

/** Largest part relayed. Parts are UPLOAD_PART_BYTES (8 MiB by default); this leaves headroom. */
const MAX_PART_BYTES = 64 * 1024 * 1024;

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
 * Relays one presigned part upload to object storage that only this computer reaches
 * (S3_PUBLIC_ORIGIN on localhost), for pages opened from another address (`lib/relay.ts`).
 * Storage checks the signature: the URL was signed for S3_PUBLIC_ORIGIN's host, which is where
 * this forwards, with the path and query unchanged. Nothing else is reachable through it.
 */
export async function PUT(request: NextRequest) {
  const env = getEnv();
  const storage = new URL(env.S3_PUBLIC_ORIGIN);
  const { pathname, search, searchParams } = request.nextUrl;
  if (!isLoopbackHost(storage.hostname) || !searchParams.has("X-Amz-Signature")) {
    return problem(404, "http-404", "Not found", "This path is not available.");
  }
  if (!isTrustedOrigin("PUT", request.headers.get("origin"), appOrigins(env))) {
    return problem(
      403,
      "forbidden-origin",
      "Request blocked",
      "Cross-site requests are not allowed.",
    );
  }
  const length = Number(request.headers.get("content-length") ?? "");
  if (!Number.isFinite(length) || length <= 0 || length > MAX_PART_BYTES) {
    return problem(
      413,
      "part-too-large",
      "Upload part rejected",
      "The upload part has no size or is too large.",
    );
  }

  let upstream: Response;
  try {
    // Buffered (one part), so storage gets a Content-Length: S3 refuses chunked part uploads.
    const body = await request.arrayBuffer();
    upstream = await fetch(
      `${storage.origin}${pathname.slice(STORAGE_RELAY_PATH.length)}${search}`,
      {
        method: "PUT",
        body,
        cache: "no-store",
        signal: AbortSignal.timeout(120_000),
      },
    );
  } catch {
    return problem(
      503,
      "storage-unavailable",
      "Storage is unavailable",
      "We couldn't reach file storage. Try again in a moment.",
    );
  }

  // Only the status and ETag: storage's error body names the bucket and key.
  const headers = new Headers({ "Cache-Control": "no-store" });
  const etag = upstream.headers.get("etag");
  if (etag) headers.set("ETag", etag);
  await upstream.body?.cancel();
  return new NextResponse(null, { status: upstream.status, headers });
}
