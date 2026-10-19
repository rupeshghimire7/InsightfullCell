/**
 * Same-origin relays for a locally run stack opened from another address (an ngrok tunnel, a LAN
 * address). Object storage and the API then listen on localhost, which only this computer
 * reaches, so the browser sends upload parts and the progress stream through the web app instead
 * (`/api/storage/…`, `/api/runs/{id}/events`). With browser-facing storage and API (production)
 * nothing is relayed. ADR-039.
 */

/** Where the web app relays presigned upload parts to object storage. */
export const STORAGE_RELAY_PATH = "/api/storage";

/** True for addresses only this computer reaches: localhost, *.localhost, 127.x, [::1]. */
export function isLoopbackHost(hostname: string): boolean {
  return (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname === "[::1]" ||
    hostname === "::1" ||
    /^127\./.test(hostname)
  );
}

/** True when the page at `pageHostname` cannot reach `url` directly: it is loopback, the page is not. */
export function needsRelay(url: string, pageHostname: string): boolean {
  return isLoopbackHost(new URL(url).hostname) && !isLoopbackHost(pageHostname);
}

/**
 * The URL the browser PUTs a presigned part to. The presigned URL is signed for the storage host
 * (the relay forwards to that same host), so only its origin changes; path and query stay as signed.
 */
export function partUploadUrl(url: string, pageHostname: string): string {
  if (!needsRelay(url, pageHostname)) return url;
  const target = new URL(url);
  return `${STORAGE_RELAY_PATH}${target.pathname}${target.search}`;
}

/** Where the browser opens a run's progress stream when it cannot reach the API itself. */
export function eventsRelayPath(runId: string): string {
  return `/api/runs/${runId}/events`;
}
