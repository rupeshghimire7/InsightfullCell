export interface CspOptions {
  nonce: string;
  /** Browser-facing origin of object storage (presigned PUTs go there). */
  storageOrigin: string;
  /** Browser-facing origin of the API (live-progress streams connect there). */
  apiOrigin?: string;
  development?: boolean;
  /** Only behind HTTPS: upgrading plain-HTTP requests would break local http:// storage. */
  upgradeInsecureRequests?: boolean;
}

/**
 * Content-Security-Policy with a per-request nonce. Scripts run only if they carry the nonce
 * (`strict-dynamic` lets those scripts load their chunks); `wasm-unsafe-eval` allows the SHA-256
 * WebAssembly module, but not `eval`.
 */
export function buildCsp({
  nonce,
  storageOrigin,
  apiOrigin,
  development = false,
  upgradeInsecureRequests = false,
}: CspOptions): string {
  const origin = new URL(storageOrigin).origin;
  const api = apiOrigin ? [new URL(apiOrigin).origin] : [];
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": [
      "'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      "'wasm-unsafe-eval'",
      ...(development ? ["'unsafe-eval'"] : []),
    ],
    "style-src": ["'self'", development ? "'unsafe-inline'" : `'nonce-${nonce}'`],
    // React `style={{ width }}` attributes (progress bars) are style attributes, not elements.
    "style-src-attr": ["'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": ["'self'", "data:"],
    "connect-src": [
      "'self'",
      origin,
      ...api,
      ...(development ? ["ws:", "http://localhost:*"] : []),
    ],
    "worker-src": ["'self'", "blob:"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    ...(upgradeInsecureRequests ? { "upgrade-insecure-requests": [] } : {}),
  };
  return Object.entries(directives)
    .map(([name, values]) => [name, ...values].join(" "))
    .join("; ");
}

export function generateNonce(): string {
  return btoa(crypto.randomUUID());
}
