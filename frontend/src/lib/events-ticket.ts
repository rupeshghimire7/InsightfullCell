/**
 * Run-scoped event-stream tickets (IC-008). The browser opens a run's progress stream on the API
 * directly, so it needs a credential; it must never hold the API token. A ticket is a separate,
 * 5-minute EdDSA JWT with its own audience and exactly one run id: it opens that run's stream and
 * nothing else (the API checks the audience on both sides, and the run under row-level security).
 */

export const TICKET_SECONDS = 300;

/** The identity claims of the caller's API token, which the ticket repeats. */
export interface IdentityClaims {
  sub: string;
  org_id: string;
  role: string;
  org_name?: string;
}

/**
 * Reads the payload of a JWT *this server just minted* (from `getApiToken`). The signature is not
 * checked here because the token never left the server; the API verifies the ticket itself.
 */
export function identityFromApiToken(token: string): IdentityClaims | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  let claims: unknown;
  try {
    const base64 = payload.replaceAll("-", "+").replaceAll("_", "/");
    claims = JSON.parse(atob(base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=")));
  } catch {
    return null;
  }
  if (typeof claims !== "object" || claims === null) return null;
  const { sub, org_id, role, org_name } = claims as Record<string, unknown>;
  if (typeof sub !== "string" || typeof org_id !== "string" || typeof role !== "string") {
    return null;
  }
  return { sub, org_id, role, ...(typeof org_name === "string" ? { org_name } : {}) };
}

export function ticketClaims(
  identity: IdentityClaims,
  runId: string,
  audience: string,
  nowSeconds: number,
): Record<string, string | number> {
  return {
    ...identity,
    run_id: runId,
    aud: audience,
    iat: nowSeconds,
    exp: nowSeconds + TICKET_SECONDS,
  };
}
