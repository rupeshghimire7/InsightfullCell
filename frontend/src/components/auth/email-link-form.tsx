"use client";

import Link from "next/link";
import { useId, useRef, useState, type FormEvent } from "react";
import {
  EMAIL_PATTERN,
  settle,
  transportError,
  useHydrated,
  type AuthError,
  type LinkDelivery,
} from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";
import { EMAIL_NOT_ALLOWED_MESSAGE } from "@/lib/auth-constants";

type Status =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "sent"; email: string }
  | { kind: "not-allowed" }
  | { kind: "error"; message: string };

/** Why a sign-in link was not sent, from the server's error code (see `auth-options.ts`). */
export function linkError(error: AuthError, delivery: LinkDelivery): string {
  if (error.code === "RESEND_RECIPIENT_NOT_ALLOWED") {
    return "We couldn’t send the link: email is in testing mode and only reaches the project owner’s address.";
  }
  if (error.code === "EMAIL_NOT_CONFIGURED") {
    return "Sign-in email isn’t set up correctly on this server. Use your password or Google instead.";
  }
  // A 5xx here is usually the email failing to send, which the delivery-specific text explains.
  const transport = error.status && error.status >= 500 ? null : transportError(error);
  if (transport) return transport;
  return delivery === "resend-test"
    ? "We couldn’t send the link. While email is in testing mode, links only reach the project owner’s address."
    : "We couldn’t send the link. Check the address and try again.";
}

/**
 * Asks for the address and sends a sign-in link to it. An address the server does not send links
 * to (`EMAIL_LINK_ALLOWED_EMAILS`) gets the explanation and a way to register with a password.
 */
export function EmailLinkForm({
  delivery,
  expiresInMinutes,
}: {
  delivery: LinkDelivery;
  expiresInMinutes: number;
}) {
  const hydrated = useHydrated();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [fieldError, setFieldError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const email = (inputRef.current?.value ?? "").trim();
    if (!EMAIL_PATTERN.test(email)) {
      setFieldError("Enter your email address, like name@company.com.");
      inputRef.current?.focus();
      return;
    }
    setFieldError(null);
    setStatus({ kind: "busy" });
    const error = await settle(() =>
      authClient.signIn.magicLink({
        email,
        callbackURL: "/",
        // A failed or reused link lands on the sign-in page, where the error is explained.
        errorCallbackURL: "/sign-in",
      }),
    );
    if (!error) {
      setStatus({ kind: "sent", email });
      return;
    }
    if (error.code === "EMAIL_NOT_ALLOWED") {
      setStatus({ kind: "not-allowed" });
      return;
    }
    setStatus({ kind: "error", message: linkError(error, delivery) });
  }

  if (status.kind === "sent") {
    const toLog = delivery === "log";
    return (
      <div role="status" className="space-y-3">
        <h2 className="text-xl font-semibold">
          {toLog ? "Check the server terminal" : "Check your inbox"}
        </h2>
        <p>
          {toLog ? (
            <>
              Testing mode: no email was sent. The sign-in link for{" "}
              <strong className="font-semibold">{status.email}</strong> is printed in the terminal
              running the web app.
            </>
          ) : (
            <>
              We sent a sign-in link to <strong className="font-semibold">{status.email}</strong>.
            </>
          )}{" "}
          It expires in {expiresInMinutes}&nbsp;minutes and works once.
        </p>
        {delivery === "resend-test" ? (
          <p className="text-sm text-muted">If nothing comes within a minute, check spam.</p>
        ) : null}
        <Button variant="secondary" onClick={() => setStatus({ kind: "idle" })}>
          Use a different email
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {status.kind === "not-allowed" ? (
        <ProblemAlert
          title="This email can’t get a sign-in link"
          detail={EMAIL_NOT_ALLOWED_MESSAGE}
          action={
            <Link
              href="/sign-in?mode=register"
              className="inline-flex min-h-9 items-center rounded-control bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110"
            >
              Register with a password
            </Link>
          }
        />
      ) : null}
      {status.kind === "error" ? (
        <ProblemAlert title="The link wasn’t sent" detail={status.message} />
      ) : null}
      <form onSubmit={onSubmit} method="post" noValidate className="space-y-4">
        <div>
          <label htmlFor={inputId} className="block text-sm font-medium">
            Email address
          </label>
          <input
            ref={inputRef}
            id={inputId}
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={254}
            placeholder="name@company.com…"
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? errorId : undefined}
            className="mt-1.5 block min-h-10 w-full rounded-control border border-border bg-surface px-3 text-sm transition-colors placeholder:text-muted hover:border-muted/60 focus-visible:border-accent aria-invalid:border-danger pointer-coarse:min-h-11"
          />
          {fieldError ? (
            <p id={errorId} className="mt-1 text-sm text-danger">
              {fieldError}
            </p>
          ) : null}
        </div>
        <Button
          type="submit"
          loading={status.kind === "busy"}
          disabled={!hydrated}
          className="w-full"
        >
          {status.kind === "busy" ? "Sending…" : "Send sign-in link"}
        </Button>
      </form>
    </div>
  );
}
