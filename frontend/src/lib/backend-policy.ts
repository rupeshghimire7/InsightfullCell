/**
 * What the browser may ask the Next.js server to forward to the API. Pure functions so the
 * rules are unit-tested; the route handler only wires them to `fetch`.
 */

const ALLOWED_PREFIXES = ["v1/me", "v1/uploads", "v1/datasets", "v1/runs"] as const;
/**
 * Event streams are opened by the browser on the API directly with a run-scoped ticket; through
 * this proxy they would hold a web function open for the length of a run.
 */
const DENIED = /^v1\/runs\/[^/]+\/events$/;
const SEGMENT = /^[A-Za-z0-9._~:-]+$/;
const MAX_SEGMENTS = 4;

/** Returns the API path (no leading slash) for a catch-all route, or `null` if not allowed. */
export function resolveBackendPath(segments: readonly string[]): string | null {
  if (segments.length === 0 || segments.length > MAX_SEGMENTS) return null;
  for (const segment of segments) {
    if (!SEGMENT.test(segment) || segment === "." || segment === "..") return null;
  }
  const path = segments.join("/");
  // `:action` suffixes (v1/uploads/{id}:complete) arrive attached to the id segment.
  const allowed = ALLOWED_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
  return allowed && !DENIED.test(path) ? path : null;
}

export const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "idempotency-key",
  "if-match",
] as const;
export const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "x-request-id",
  "retry-after",
  "etag",
  "location",
  "content-disposition",
] as const;

/**
 * Largest request body forwarded to the API. Everything the browser sends through this proxy is
 * small JSON (file bytes go to storage directly), so a bigger body is refused before it is read.
 */
export const MAX_BODY_BYTES = 2 * 1024 * 1024;

/** Whether a declared `Content-Length` is over the limit (a missing or bad one is not). */
export function declaredBodyTooLarge(contentLength: string | null): boolean {
  const length = Number(contentLength ?? "");
  return Number.isFinite(length) && length > MAX_BODY_BYTES;
}

/**
 * Mutations must come from this app's own pages (defence in depth next to SameSite=Lax), at any
 * of the origins it is served at (`appOrigins`).
 */
export function isTrustedOrigin(
  method: string,
  originHeader: string | null,
  appOrigins: readonly string[],
): boolean {
  if (method === "GET" || method === "HEAD") return true;
  return originHeader !== null && appOrigins.includes(originHeader);
}
