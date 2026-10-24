"use server";

import { isAPIError } from "better-auth/api";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/lib/auth";
import { serverApi } from "@/lib/backend";
import { getSession } from "@/lib/session";

export interface DeleteAccountState {
  error?: string;
  field?: "password";
}

/**
 * "Delete my account" (ADR-043), in an order that never leaves data behind an account that is
 * gone: the password is checked, then the API erases the workspace (datasets, analyses, uploads,
 * counters; its files at the next sweep), then Better Auth deletes the user, its sign-in
 * accounts and sessions, and `beforeDelete` the workspace and every pending code or link.
 */
export async function deleteAccountAction(
  _previous: DeleteAccountState,
  form: FormData,
): Promise<DeleteAccountState> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  const password = String(form.get("password") ?? "");
  if (!password) return { error: "Enter your password.", field: "password" };
  const requestHeaders = await headers();
  const auth = getAuth();

  try {
    await auth.api.verifyPassword({ body: { password }, headers: requestHeaders });
  } catch (error) {
    if (!isAPIError(error)) throw error;
    return { error: "That password isn’t right.", field: "password" };
  }

  const { response } = await serverApi().DELETE("/v1/me");
  if (response.status === 409) {
    return {
      error:
        "An analysis is still running. Cancel it, wait until it has stopped, then delete your account. Nothing was deleted.",
    };
  }
  if (!response.ok) {
    console.error(`[account] workspace delete failed: HTTP ${response.status}`);
    return { error: "We couldn’t delete your data. Nothing was deleted; try again in a moment." };
  }

  try {
    await auth.api.deleteUser({ body: { password }, headers: requestHeaders });
  } catch (error) {
    console.error(
      `[account] user delete failed after the workspace was erased: ${isAPIError(error) ? error.status : "unknown error"}`,
    );
    return {
      error:
        "Your data was deleted, but we couldn’t close the account itself. Try again to finish.",
    };
  }
  redirect("/sign-in?account=deleted");
}
