"use client";

import { useRef, useState, type FormEvent } from "react";
import { NewPasswordFields, useNewPassword } from "@/components/auth/new-password-fields";
import {
  Field,
  PasswordInput,
  settle,
  transportError,
  useHydrated,
} from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";

type Errors = Partial<Record<"current" | "next" | "confirm", string>>;
type Status = { kind: "idle" | "busy" | "done" } | { kind: "error"; message: string };

/**
 * Change the password: the current one, the new one twice, with the shared policy and its live
 * checklist. Other devices are signed out, so a password someone else learned stops working
 * everywhere at once.
 */
export function ChangePasswordForm({ email }: { email?: string }) {
  const form = useRef<HTMLFormElement>(null);
  const hydrated = useHydrated();
  const fresh = useNewPassword(email);
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const data = new FormData(event.currentTarget);
    const current = String(data.get("currentPassword") ?? "");
    const next = fresh.password;
    const found: Errors = {};
    if (!current) found.current = "Enter your current password.";
    if (fresh.problem) found.next = fresh.problem;
    else if (next === current) found.next = "Choose a password different from the current one.";
    if (!found.next && fresh.confirmProblem) found.confirm = fresh.confirmProblem;
    setErrors(found);
    if (Object.keys(found).length > 0) {
      // By name: the inputs are not marked invalid until React renders the errors.
      const first = found.current
        ? "currentPassword"
        : found.next
          ? "newPassword"
          : "confirmPassword";
      form.current?.querySelector<HTMLInputElement>(`[name="${first}"]`)?.focus();
      return;
    }
    setStatus({ kind: "busy" });
    const error = await settle(() =>
      authClient.changePassword({
        currentPassword: current,
        newPassword: next,
        revokeOtherSessions: true,
      }),
    );
    if (!error) {
      form.current?.reset();
      fresh.reset();
      setStatus({ kind: "done" });
      return;
    }
    const message =
      transportError(error) ??
      (error.code === "INVALID_PASSWORD"
        ? "Your current password isn’t right."
        : error.code === "PASSWORD_TOO_WEAK" && error.message
          ? error.message
          : "We couldn’t change your password. Try again.");
    setStatus({ kind: "error", message });
  }

  return (
    <form ref={form} onSubmit={onSubmit} method="post" noValidate className="max-w-sm space-y-4">
      {status.kind === "error" ? (
        <ProblemAlert title="The password wasn’t changed" detail={status.message} />
      ) : null}
      {status.kind === "done" ? (
        <p
          role="status"
          className="rounded-control border border-accent/40 bg-accent/10 px-3 py-2 text-sm"
        >
          Your password was changed. Other devices were signed out.
        </p>
      ) : null}
      <Field label="Current password" error={errors.current}>
        {(props) => (
          <PasswordInput {...props} name="currentPassword" autoComplete="current-password" />
        )}
      </Field>
      <NewPasswordFields state={fresh} email={email} errors={errors} />
      <Button type="submit" loading={status.kind === "busy"} disabled={!hydrated}>
        {status.kind === "busy" ? "Changing password…" : "Change password"}
      </Button>
    </form>
  );
}
