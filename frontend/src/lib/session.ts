import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth, getAuthPool } from "@/lib/auth";
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

/**
 * Whether the user has a password (Better Auth's `credential` account, with a hash). People who
 * registered with an emailed code, Google or a sign-in link have none until `/set-password`.
 * Deduplicated per request; an unreachable database throws `ServiceUnavailableError`.
 */
export const hasPassword = cache(async (userId: string): Promise<boolean> => {
  try {
    const { rows } = await getAuthPool().query(
      `SELECT 1 FROM account
        WHERE "userId" = $1 AND "providerId" = 'credential' AND password IS NOT NULL
        LIMIT 1`,
      [userId],
    );
    return rows.length > 0;
  } catch (error) {
    if (isConnectionFailure(error)) throw new ServiceUnavailableError(undefined, { cause: error });
    throw error;
  }
});

/**
 * The signed-in app is for people with a password (ADR-042): anyone without one is sent to choose
 * it first. Called by the server layouts of the signed-in area, never by `/set-password` itself.
 */
export async function requirePassword(session: AppSession): Promise<void> {
  if (!(await hasPassword(session.userId))) redirect("/set-password");
}

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
