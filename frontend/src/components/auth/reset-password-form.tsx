"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { NewPasswordFields, useNewPassword } from "@/components/auth/new-password-fields";
import { settle, transportError, useHydrated } from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";

type Errors = Partial<Record<"next" | "confirm", string>>;
type Status = { kind: "idle" | "busy" | "expired" } | { kind: "error"; message: string };

export const RESET_LINK_EXPIRED =
  "That reset link has expired or was already used. Ask for a new one.";

/**
 * Sets a new password with the token from an emailed reset link: the new password twice, with the
 * shared policy and its live checklist (the address is not known here, so the server alone checks
 * that the password does not contain it). The token works once; every session of the
 * account is signed out, so whoever knew the old password loses access at once. Once saved, it goes
 * straight to the sign-in page, which says the password was changed.
 */
export function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const form = useRef<HTMLFormElement>(null);
  const hydrated = useHydrated();
  const fresh = useNewPassword();
  const [errors, setErrors] = useState<Errors>({});
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const next = fresh.password;
    const found: Errors = {};
    if (fresh.problem) found.next = fresh.problem;
    else if (fresh.confirmProblem) found.confirm = fresh.confirmProblem;
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
      // Stays "busy" while the sign-in page loads, so the form cannot be sent twice.
      router.replace("/sign-in?reset=done");
      return;
    }
    if (error.code === "INVALID_TOKEN") {
      setStatus({ kind: "expired" });
      return;
    }
    if (error.code === "PASSWORD_TOO_WEAK" && error.message) {
      setErrors({ next: error.message });
      form.current?.querySelector<HTMLInputElement>('[name="newPassword"]')?.focus();
      setStatus({ kind: "idle" });
      return;
    }
    setStatus({
      kind: "error",
      message: transportError(error) ?? "We couldn’t change your password. Try again.",
    });
  }

  if (status.kind === "expired") return <ResetLinkExpired />;

  return (
    <form ref={form} onSubmit={onSubmit} method="post" noValidate className="space-y-4">
      {status.kind === "error" ? (
        <ProblemAlert title="The password wasn’t changed" detail={status.message} />
      ) : null}
      <NewPasswordFields state={fresh} errors={errors} />
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
