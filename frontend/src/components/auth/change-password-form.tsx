"use client";

import { useRef, useState, type FormEvent } from "react";
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
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-constants";

type Errors = Partial<Record<"current" | "next" | "confirm", string>>;
type Status = { kind: "idle" | "busy" | "done" } | { kind: "error"; message: string };

/**
 * Change the password: the current one, the new one twice. Other devices are signed out, so a
 * password someone else learned stops working everywhere at once.
 */
export function ChangePasswordForm() {
  const form = useRef<HTMLFormElement>(null);
  const hydrated = useHydrated();
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const data = new FormData(event.currentTarget);
    const current = String(data.get("currentPassword") ?? "");
    const next = String(data.get("newPassword") ?? "");
    const confirm = String(data.get("confirmPassword") ?? "");
    const found: Errors = {};
    if (!current) found.current = "Enter your current password.";
    if (next.length < PASSWORD_MIN_LENGTH) {
      found.next = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    } else if (next.length > PASSWORD_MAX_LENGTH) {
      found.next = `Use at most ${PASSWORD_MAX_LENGTH} characters.`;
    } else if (next === current) {
      found.next = "Choose a password different from the current one.";
    }
    if (!found.next && confirm !== next) found.confirm = "The passwords don’t match.";
    setErrors(found);
    if (Object.keys(found).length > 0) {
      form.current?.querySelector<HTMLInputElement>("[aria-invalid=true]")?.focus();
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
      setStatus({ kind: "done" });
      return;
    }
    const message =
      transportError(error) ??
      (error.code === "INVALID_PASSWORD"
        ? "Your current password isn’t right."
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
      <Field
        label="New password"
        error={errors.next}
        hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
      >
        {(props) => <PasswordInput {...props} name="newPassword" autoComplete="new-password" />}
      </Field>
      <Field label="Confirm new password" error={errors.confirm}>
        {(props) => <PasswordInput {...props} name="confirmPassword" autoComplete="new-password" />}
      </Field>
      <Button type="submit" loading={status.kind === "busy"} disabled={!hydrated}>
        {status.kind === "busy" ? "Changing password…" : "Change password"}
      </Button>
    </form>
  );
}
