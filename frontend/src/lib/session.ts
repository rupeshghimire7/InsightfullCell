import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/lib/auth";
import { isConnectionFailure, ServiceUnavailableError } from "@/lib/unavailable";

export interface AppSession {
  userId: string;
  email: string;
  name: string;
  organizationId: string;
}

/**
 * The signed-in user and active organisation, or `null`. Deduplicated per request.
 * Throws `ServiceUnavailableError` when the auth database cannot be reached: an outage is not
 * "signed out", and pages render it as such (see `ServiceUnavailable`).
 */
export const getSession = cache(async (): Promise<AppSession | null> => {
  // Reading headers first marks every page that needs a session as dynamic, so `next build`
  // never tries to render it (and never needs secrets).
  const requestHeaders = await headers();
  let result;
  try {
    result = await getAuth().api.getSession({ headers: requestHeaders });
  } catch (error) {
    if (isConnectionFailure(error)) {
      console.error("[auth] session lookup failed: the auth database is unreachable");
      throw new ServiceUnavailableError(undefined, { cause: error });
    }
    throw error;
  }
  if (!result) return null;
  const organizationId = (result.session as { activeOrganizationId?: string | null })
    .activeOrganizationId;
  if (!organizationId) return null;
  return {
    userId: result.user.id,
    email: result.user.email,
    name: result.user.name,
    organizationId,
  };
});

export async function requireSession(): Promise<AppSession> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  return session;
}

/** `getSession`, with an outage returned as `"unavailable"` instead of thrown. */
export async function getSessionOrUnavailable(): Promise<AppSession | null | "unavailable"> {
  try {
    return await getSession();
  } catch (error) {
    if (error instanceof ServiceUnavailableError) return "unavailable";
    throw error;
  }
}
