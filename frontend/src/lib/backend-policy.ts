/**
 * What the browser may ask the Next.js server to forward to the API. Pure functions so the
 * rules are unit-tested; the route handler only wires them to `fetch`.
 */

const ALLOWED_PREFIXES = ["v1/me", "v1/uploads", "v1/datasets"] as const;
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
  return allowed ? path : null;
}

export const FORWARDED_REQUEST_HEADERS = ["accept", "content-type", "idempotency-key"] as const;
export const FORWARDED_RESPONSE_HEADERS = ["content-type", "x-request-id", "retry-after"] as const;

/** Mutations must come from this app's own pages (defence in depth next to SameSite=Lax). */
export function isTrustedOrigin(
  method: string,
  originHeader: string | null,
  appOrigin: string,
): boolean {
  if (method === "GET" || method === "HEAD") return true;
  return originHeader !== null && originHeader === appOrigin;
}
