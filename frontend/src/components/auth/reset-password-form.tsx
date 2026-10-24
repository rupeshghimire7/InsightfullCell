"use client";

import Link from "next/link";
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

type Errors = Partial<Record<"next" | "confirm", string>>;
type Status = { kind: "idle" | "busy" | "done" | "expired" } | { kind: "error"; message: string };

export const RESET_LINK_EXPIRED =
  "That reset link has expired or was already used. Ask for a new one.";

const linkClass =
  "inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11";

/**
 * Sets a new password with the token from an emailed reset link: the new password twice, with the
 * same rules as registration and the Profile page. The token works once; every session of the
 * account is signed out, so whoever knew the old password loses access at once.
 */
export function ResetPasswordForm({ token }: { token: string }) {
  const form = useRef<HTMLFormElement>(null);
  const hydrated = useHydrated();
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const data = new FormData(event.currentTarget);
    const next = String(data.get("newPassword") ?? "");
    const confirm = String(data.get("confirmPassword") ?? "");
    const found: Errors = {};
    if (next.length < PASSWORD_MIN_LENGTH) {
      found.next = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    } else if (next.length > PASSWORD_MAX_LENGTH) {
      found.next = `Use at most ${PASSWORD_MAX_LENGTH} characters.`;
    }
    if (!found.next && confirm !== next) found.confirm = "The passwords don’t match.";
    setErrors(found);
    if (Object.keys(found).length > 0) {
      // By name: the inputs are not marked invalid until React renders the errors.
      const first = found.next ? "newPassword" : "confirmPassword";
      form.current?.querySelector<HTMLInputElement>(`[name="${first}"]`)?.focus();
      return;
    }
    setStatus({ kind: "busy" });
    const error = await settle(() => authClient.resetPassword({ newPassword: next, token }));
    if (!error) {
      setStatus({ kind: "done" });
      return;
    }
    if (error.code === "INVALID_TOKEN") {
      setStatus({ kind: "expired" });
      return;
    }
    setStatus({
      kind: "error",
      message: transportError(error) ?? "We couldn’t change your password. Try again.",
    });
  }

  if (status.kind === "done") {
    return (
      <div role="status" className="space-y-3">
        <h2 className="text-xl font-semibold">Password changed</h2>
        <p>Sign in with your new password. Every device that was signed in has been signed out.</p>
        <p>
          <Link href="/sign-in" className={linkClass}>
            Sign in
          </Link>
        </p>
      </div>
    );
  }
  if (status.kind === "expired") return <ResetLinkExpired />;

  return (
    <form ref={form} onSubmit={onSubmit} method="post" noValidate className="space-y-4">
      {status.kind === "error" ? (
        <ProblemAlert title="The password wasn’t changed" detail={status.message} />
      ) : null}
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
      <Button
        type="submit"
        loading={status.kind === "busy"}
        disabled={!hydrated}
        className="w-full"
      >
        {status.kind === "busy" ? "Changing password…" : "Change password"}
      </Button>
    </form>
  );
}

/** A reset link that is missing, expired or already used, with the way to a new one. */
export function ResetLinkExpired() {
  return (
    <ProblemAlert
      title="This link can’t be used"
      detail={RESET_LINK_EXPIRED}
      action={
        <Link
          href="/sign-in/forgot"
          className="inline-flex min-h-9 items-center rounded-control bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110"
        >
          Get a new link
        </Link>
      }
    />
  );
}
