"use server";

import { isAPIError } from "better-auth/api";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/lib/auth";
import { passwordPolicyError } from "@/lib/password-policy";
import { getSession, hasPassword } from "@/lib/session";

export interface SetPasswordState {
  error?: string;
  field?: "newPassword" | "confirmPassword";
}

/**
 * Sets the first password of the signed-in user (ADR-042). Better Auth's `setPassword` is
 * server-only, so the browser reaches it only through this action: the session is checked, the
 * password is checked with the shared policy (the server hook checks it again), and nothing else
 * changes: no other session is revoked. Then the person enters the app.
 */
export async function setPasswordAction(
  _previous: SetPasswordState,
  form: FormData,
): Promise<SetPasswordState> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  const password = String(form.get("newPassword") ?? "");
  const confirm = String(form.get("confirmPassword") ?? "");
  const problem = passwordPolicyError(password, session.email);
  if (problem) return { error: problem, field: "newPassword" };
  if (confirm !== password)
    return { error: "The passwords don’t match.", field: "confirmPassword" };
  if (await hasPassword(session.userId)) redirect("/");
  try {
    await getAuth().api.setPassword({
      body: { newPassword: password },
      headers: await headers(),
    });
  } catch (error) {
    if (!isAPIError(error)) throw error;
    const code = (error.body as { code?: string } | undefined)?.code;
    if (code === "PASSWORD_ALREADY_SET") redirect("/");
    if (code === "PASSWORD_TOO_WEAK") return { error: error.message, field: "newPassword" };
    console.error(`[auth] set password failed: ${code ?? error.status}`);
    return { error: "We couldn’t save your password. Try again." };
  }
  redirect("/");
}
