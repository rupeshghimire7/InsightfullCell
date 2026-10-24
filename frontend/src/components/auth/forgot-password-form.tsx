"use client";

import { useRef, useState, type FormEvent } from "react";
import {
  EMAIL_PATTERN,
  Field,
  settle,
  transportError,
  useHydrated,
} from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";

type Status =
  { kind: "idle" | "busy" } | { kind: "sent"; email: string } | { kind: "error"; message: string };

/** Where the emailed link lands once Better Auth has checked its token. */
export const RESET_PAGE = "/sign-in/reset";

/**
 * Asks for the address a password-reset link goes to. Whatever the server answers, the same
 * message is shown, so the page never tells whether an address has an account. Only a request
 * that never got an answer (or was refused before it was read: rate limit, wrong web address)
 * says so, because nothing was sent to anyone.
 */
export function ForgotPasswordForm({
  expiresInMinutes,
  toLog,
}: {
  expiresInMinutes: number;
  /** Local development: links are printed in the web server's terminal, never emailed. */
  toLog: boolean;
}) {
  const form = useRef<HTMLFormElement>(null);
  const hydrated = useHydrated();
  const [fieldError, setFieldError] = useState<string>();
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const email = String(new FormData(event.currentTarget).get("email") ?? "").trim();
    if (!EMAIL_PATTERN.test(email)) {
      setFieldError("Enter your email address, like name@company.com.");
      form.current?.querySelector<HTMLInputElement>('[name="email"]')?.focus();
      return;
    }
    setFieldError(undefined);
    setStatus({ kind: "busy" });
    const error = await settle(() =>
      authClient.requestPasswordReset({ email, redirectTo: RESET_PAGE }),
    );
    const transport = error ? transportError(error) : null;
    setStatus(transport ? { kind: "error", message: transport } : { kind: "sent", email });
  }

  if (status.kind === "sent") {
    return (
      <div role="status" className="space-y-3">
        <h2 className="text-xl font-semibold">Check your inbox</h2>
        <p>
          If an account uses <strong className="font-semibold">{status.email}</strong>, we’ve
          emailed a link to reset the password. It works for {expiresInMinutes}&nbsp;minutes, once.
        </p>
        {toLog ? (
          <p className="text-sm text-muted">
            Testing mode: no email is sent. The link is printed in the terminal running the web app.
          </p>
        ) : (
          <p className="text-sm text-muted">If nothing comes within a few minutes, check spam.</p>
        )}
        <Button variant="secondary" onClick={() => setStatus({ kind: "idle" })}>
          Use a different email
        </Button>
      </div>
    );
  }

  return (
    <form ref={form} onSubmit={onSubmit} method="post" noValidate className="space-y-4">
      {status.kind === "error" ? (
        <ProblemAlert title="The link wasn’t sent" detail={status.message} />
      ) : null}
      <Field label="Email address" error={fieldError}>
        {(props) => (
          <input
            {...props}
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={254}
            placeholder="name@company.com…"
          />
        )}
      </Field>
      <Button
        type="submit"
        loading={status.kind === "busy"}
        disabled={!hydrated}
        className="w-full"
      >
        {status.kind === "busy" ? "Sending…" : "Email me a reset link"}
      </Button>
    </form>
  );
}
