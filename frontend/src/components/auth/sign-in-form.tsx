"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Info } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-constants";

export type SignInMode = "sign-in" | "register";
type Action = "password" | "link" | "google";
type Status =
  | { kind: "idle" }
  | { kind: "busy"; action: Action }
  | { kind: "sent"; email: string; via: "log" | "email" | "test-sender" }
  | { kind: "error"; message: string };

type FieldName = "name" | "email" | "password" | "resendApiKey";
type FieldErrors = Partial<Record<FieldName, string>>;
interface Values {
  name: string;
  email: string;
  password: string;
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
/** Same rule as the server (`isResendApiKey`); the server checks again. */
const RESEND_KEY_PATTERN = /^re_[A-Za-z0-9_-]{8,200}$/;
const TOO_MANY = "Too many attempts. Wait a minute, then try again.";
const TEST_SENDER_LIMIT =
  "Resend’s test sender only delivers to the email address that owns the Resend account. Enter that address, or verify a domain in Resend to email anyone.";

/**
 * Where sign-in links go. `log`: printed in the web server's terminal, never emailed.
 * `resend-test`: emailed from Resend's shared test sender, which only delivers to the Resend
 * account owner. `email`: emailed from a verified domain, to anyone. `off`: sign-in links are
 * switched off (the button is shown disabled, with a note saying so).
 */
export type LinkDelivery = "log" | "resend-test" | "email" | "off";
/** `setup-needed`: shown disabled with setup steps (development only). */
export type GoogleSignIn = "on" | "setup-needed" | "off";

export interface SignInFormProps {
  mode?: SignInMode;
  google: GoogleSignIn;
  linkDelivery: LinkDelivery;
  /** Message from a failed link verification, shown above the form. */
  initialError?: string;
  expiresInMinutes: number;
}

/** What is wrong with the fields an action needs, in field order. Empty when all are valid. */
export function validate(mode: SignInMode, action: "password" | "link", values: Values) {
  const errors: FieldErrors = {};
  if (mode === "register" && !values.name) errors.name = "Enter your name.";
  if (!EMAIL_PATTERN.test(values.email)) {
    errors.email = "Enter your email address, like name@company.com.";
  }
  if (action === "password") {
    if (mode === "sign-in" && !values.password) errors.password = "Enter your password.";
    if (mode === "register" && values.password.length < PASSWORD_MIN_LENGTH) {
      errors.password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    }
  }
  return errors;
}

function passwordError(mode: SignInMode, error: { status?: number; code?: string }): string {
  if (error.status === 429) return TOO_MANY;
  if (mode === "register") {
    return error.code?.startsWith("USER_ALREADY_EXISTS")
      ? "An account with this email already exists. Sign in instead."
      : "We couldn’t create your account. Try again.";
  }
  return error.status === 401
    ? "That email and password don’t match. If you signed up with an emailed link or Google, use that instead."
    : "We couldn’t sign you in. Try again.";
}

/** Why a sign-in link was not sent, from the server's error code (see `auth-options.ts`). A
 * problem with the typed Resend key is shown on that field instead of above the form. */
export function linkError(
  error: { status?: number; code?: string },
  delivery: LinkDelivery,
): { message: string; field?: "resendApiKey" } {
  switch (error.code) {
    case "RESEND_KEY_INVALID":
      return {
        field: "resendApiKey",
        message: "Resend didn’t accept this API key. Check it (it starts with re_) and try again.",
      };
    case "RESEND_RECIPIENT_NOT_ALLOWED":
      return { message: `We couldn’t send the link. ${TEST_SENDER_LIMIT}` };
    case "EMAIL_NOT_CONFIGURED":
      return {
        message:
          "Sign-in email isn’t set up correctly on this server. Paste your own Resend API key, or use a password.",
      };
  }
  if (error.status === 429) return { message: TOO_MANY };
  return {
    message:
      delivery === "resend-test"
        ? "We couldn’t send the link. While email is in testing mode, links only reach the Resend account owner’s address."
        : "We couldn’t send the link. Check the address and try again.",
  };
}

export function SignInForm({
  mode = "sign-in",
  google,
  linkDelivery,
  initialError,
  expiresInMinutes,
}: SignInFormProps) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  /** Testing mode: the step that asks for the tester's own Resend API key is open. */
  const [askingForKey, setAskingForKey] = useState(false);
  const registering = mode === "register";
  const testingEmail = linkDelivery !== "email";
  const linksOff = linkDelivery === "off";

  // Opening the key step moves focus into it; closing it returns focus to the button that opened
  // it. Skipped on the first render so the page does not grab focus on load.
  const keyStepMounted = useRef(false);
  useEffect(() => {
    if (!keyStepMounted.current) {
      keyStepMounted.current = true;
      return;
    }
    const target = askingForKey ? '[name="resendApiKey"]' : "[data-link-button]";
    formRef.current?.querySelector<HTMLElement>(target)?.focus();
  }, [askingForKey]);

  /** Reads the form, shows any field errors and focuses the first; `null` when invalid. */
  function readValid(action: "password" | "link"): Values | null {
    const data = new FormData(formRef.current ?? undefined);
    const values: Values = {
      name: (data.get("name") ?? "").toString().trim(),
      email: (data.get("email") ?? "").toString().trim(),
      password: (data.get("password") ?? "").toString(),
    };
    const errors = validate(mode, action, values);
    setFieldErrors(errors);
    const first = Object.keys(errors)[0];
    if (first) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${first}"]`)?.focus();
      return null;
    }
    return values;
  }

  function enterApp() {
    router.replace("/");
    router.refresh();
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const values = readValid("password");
    if (!values) return;
    setStatus({ kind: "busy", action: "password" });
    const { error } = registering
      ? await authClient.signUp.email(values)
      : await authClient.signIn.email({ email: values.email, password: values.password });
    if (error) {
      setStatus({ kind: "error", message: passwordError(mode, error) });
      return;
    }
    enterApp();
  }

  /** "Email me a sign-in link": sends right away with real email; in testing mode it first asks
   * for the tester's own Resend API key. */
  async function onMagicLink() {
    if (status.kind === "busy" || linksOff) return;
    const values = readValid("link");
    if (!values) return;
    if (testingEmail) {
      setAskingForKey(true);
      return;
    }
    await sendLink(values.email, undefined);
  }

  async function onSendWithKey() {
    if (status.kind === "busy") return;
    const values = readValid("link");
    if (!values) return;
    const key = (new FormData(formRef.current ?? undefined).get("resendApiKey") ?? "")
      .toString()
      .trim();
    if (key && !RESEND_KEY_PATTERN.test(key)) {
      setFieldErrors({ resendApiKey: "A Resend API key starts with re_. Paste the whole key." });
      formRef.current?.querySelector<HTMLInputElement>('[name="resendApiKey"]')?.focus();
      return;
    }
    await sendLink(values.email, key || undefined);
  }

  async function sendLink(address: string, resendApiKey: string | undefined) {
    setStatus({ kind: "busy", action: "link" });
    const { error } = await authClient.signIn.magicLink({
      email: address,
      callbackURL: "/",
      // A failed or reused link lands on the sign-in page, where the error is explained.
      errorCallbackURL: "/sign-in",
      // Used once by the server to send this message; never stored (see `userResendKey`).
      ...(resendApiKey ? { metadata: { resendApiKey } } : {}),
    });
    if (error) {
      const problem = linkError(error, linkDelivery);
      if (problem.field) {
        setStatus({ kind: "idle" });
        setFieldErrors({ [problem.field]: problem.message });
        formRef.current?.querySelector<HTMLInputElement>(`[name="${problem.field}"]`)?.focus();
      } else {
        setStatus({ kind: "error", message: problem.message });
      }
      return;
    }
    setStatus({
      kind: "sent",
      email: address,
      via:
        resendApiKey || linkDelivery === "resend-test"
          ? "test-sender"
          : linkDelivery === "log"
            ? "log"
            : "email",
    });
  }

  async function onGoogle() {
    setStatus({ kind: "busy", action: "google" });
    const { error } = await authClient.signIn.social({ provider: "google", callbackURL: "/" });
    if (error) {
      setStatus({
        kind: "error",
        message: "Google sign-in didn’t complete. Try again, or use your email.",
      });
    }
  }

  if (status.kind === "sent") {
    const toLog = status.via === "log";
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
        {status.via === "test-sender" ? (
          <p className="text-sm text-muted">
            Sent from Resend’s test sender: it only arrives if this address owns the Resend account.
            If nothing comes within a minute, check spam.
          </p>
        ) : null}
        <Button
          variant="secondary"
          onClick={() => {
            setAskingForKey(false);
            setStatus({ kind: "idle" });
          }}
        >
          Use a different email
        </Button>
      </div>
    );
  }

  const busy = status.kind === "busy" ? status.action : null;
  return (
    <div className="space-y-5">
      {initialError ? <ProblemAlert title="Sign-in link problem" detail={initialError} /> : null}
      {status.kind === "error" ? (
        <ProblemAlert
          title={registering ? "Registration didn’t work" : "Sign-in didn’t work"}
          detail={status.message}
        />
      ) : null}

      <form ref={formRef} onSubmit={onSubmit} noValidate className="space-y-4">
        {registering ? (
          <Field label="Name" error={fieldErrors.name}>
            {(props) => (
              <input {...props} name="name" type="text" autoComplete="name" maxLength={100} />
            )}
          </Field>
        ) : null}
        <Field label="Email address" error={fieldErrors.email}>
          {(props) => (
            <input
              {...props}
              name="email"
              type="email"
              inputMode="email"
              autoComplete="email"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="name@company.com…"
            />
          )}
        </Field>
        <Field
          label="Password"
          error={fieldErrors.password}
          hint={registering ? `At least ${PASSWORD_MIN_LENGTH} characters.` : undefined}
        >
          {(props) => (
            <input
              {...props}
              name="password"
              type="password"
              autoComplete={registering ? "new-password" : "current-password"}
              maxLength={PASSWORD_MAX_LENGTH}
            />
          )}
        </Field>
        <Button type="submit" loading={busy === "password"} className="w-full">
          {registering
            ? busy === "password"
              ? "Creating account…"
              : "Create account"
            : busy === "password"
              ? "Signing in…"
              : "Sign in"}
        </Button>

        {/* The other ways in, after "or": Google, then the sign-in link. They stay inside the
            form (as plain buttons) because the link reads the email field above. */}
        {google !== "off" || !registering ? (
          <div className="space-y-4 pt-1">
            <div className="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>
            {google !== "off" ? (
              <GoogleButton
                setupNeeded={google === "setup-needed"}
                busy={busy === "google"}
                disabled={busy !== null && busy !== "google"}
                onClick={onGoogle}
              />
            ) : null}
            {registering ? null : askingForKey ? (
              <ResendKeyStep
                delivery={linkDelivery}
                error={fieldErrors.resendApiKey}
                busy={busy === "link"}
                disabled={busy !== null && busy !== "link"}
                onSend={onSendWithKey}
                onCancel={() => {
                  setFieldErrors({});
                  setAskingForKey(false);
                }}
              />
            ) : (
              <MagicLinkButton
                busy={busy === "link"}
                disabled={busy !== null && busy !== "link"}
                delivery={linkDelivery}
                onClick={onMagicLink}
              />
            )}
          </div>
        ) : null}
      </form>

      <p className="text-center text-sm text-muted">
        {registering ? "Already have an account? " : "New to InsightfulCell? "}
        <Link
          href={registering ? "/sign-in" : "/sign-in?mode=register"}
          className="inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
        >
          {registering ? "Sign in" : "Create an account"}
        </Link>
      </p>
    </div>
  );
}

interface FieldInputProps {
  id: string;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
  className: string;
}

/** A labelled input with an optional hint and an error the input is described by. */
function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: (props: FieldInputProps) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy || undefined,
        className:
          "mt-1.5 block min-h-10 w-full rounded-control border border-border bg-surface px-3 text-sm transition-colors placeholder:text-muted hover:border-muted/60 focus-visible:border-accent aria-invalid:border-danger pointer-coarse:min-h-11",
      })}
      {hint ? (
        <p id={hintId} className="mt-1 text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="mt-1 text-sm text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Email me a sign-in link". While links cannot reach everyone (printed to the server log, or sent
 * from Resend's test sender), a "Testing only" note says so. Shown only on hover of the button or its info icon; Escape dismisses it (WCAG 1.4.13); screen
 * readers read it through `aria-describedby`. Switched off, the button stays visible but inert
 * (`aria-disabled`), and the same note says links are not available yet.
 */
function MagicLinkButton({
  busy,
  disabled,
  delivery,
  onClick,
}: {
  busy: boolean;
  disabled: boolean;
  delivery: LinkDelivery;
  onClick: () => void;
}) {
  const noteId = useId();
  const testing = delivery !== "email";
  const off = delivery === "off";
  const [hovered, setHovered] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  // Shown only while the pointer is over the button or its info icon (screen readers get the
  // same text through aria-describedby).
  const open = testing && !dismissed && hovered;

  // Escape closes the note while it is open (WCAG 1.4.13).
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setDismissed(true);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const button = (
    <Button
      variant="secondary"
      className="w-full"
      data-link-button=""
      onClick={off ? undefined : onClick}
      loading={busy}
      disabled={off ? undefined : disabled}
      aria-disabled={off ? true : undefined}
      aria-describedby={testing ? noteId : undefined}
    >
      {busy ? "Sending…" : "Email me a sign-in link"}
    </Button>
  );
  if (!testing) return button;

  return (
    <div
      className="relative"
      onMouseEnter={() => {
        setHovered(true);
        setDismissed(false);
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="flex gap-2">
        {button}
        <span
          aria-hidden="true"
          className="inline-flex size-9 shrink-0 cursor-help items-center justify-center rounded-control border border-border bg-surface text-muted hover:bg-bg hover:text-text pointer-coarse:size-11"
        >
          <Info weight="fill" className="size-4" />
        </span>
      </div>
      {/* Padding, not margin, so the pointer can move onto the note without it closing. */}
      <div
        id={noteId}
        role="tooltip"
        hidden={!open}
        className="absolute inset-x-0 top-full z-10 pt-2"
      >
        <div className="rounded-panel border border-warning/50 bg-raised p-3 text-sm shadow-lg">
          <p className="font-semibold text-warning">{off ? "Not available yet" : "Testing only"}</p>
          {off ? (
            <p className="mt-1">
              Sign-in links arrive once email delivery is set up. For now, sign in with your
              password or Google.
            </p>
          ) : delivery === "log" ? (
            <p className="mt-1">
              No email is sent yet: the link is printed in the terminal running the web app. To get
              it by email, paste your own Resend API key in the next step.
            </p>
          ) : (
            <p className="mt-1">
              Links are sent from Resend’s test sender, which only delivers to the email address of
              the Resend account owner. You can use your own Resend API key in the next step. To
              email anyone, verify a domain in Resend and set{" "}
              <code className="font-mono text-xs">EMAIL_FROM</code> to an address on it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Testing mode only: asks for the tester's own Resend API key and states plainly that this is a
 * testing version. The key goes to our server once, to send this one message, and is not kept
 * (it is not saved in the browser either: `autocomplete="off"`, no storage).
 */
function ResendKeyStep({
  delivery,
  error,
  busy,
  disabled,
  onSend,
  onCancel,
}: {
  delivery: LinkDelivery;
  error?: string;
  busy: boolean;
  disabled: boolean;
  onSend: () => void;
  onCancel: () => void;
}) {
  const noticeId = useId();
  return (
    <fieldset
      className="space-y-3 rounded-panel border border-warning/50 bg-raised p-4"
      aria-describedby={noticeId}
    >
      <legend className="px-1 text-sm font-semibold">Email me a sign-in link</legend>
      <div id={noticeId} className="text-sm">
        <p className="font-semibold tracking-wide text-warning uppercase">
          Notice: this is just for the testing version.
        </p>
        <p className="mt-1">
          A registered domain is needed for sending email to any user. Until one is verified, links
          go out with your own Resend API key from Resend’s test sender, which only delivers to the
          email address that owns your Resend account. Create a key at{" "}
          <a
            href="https://resend.com/api-keys"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-accent underline underline-offset-2"
          >
            resend.com/api-keys
          </a>
          .
        </p>
      </div>
      <Field
        label="Your Resend API key"
        error={error}
        hint={
          delivery === "log"
            ? "Used once to send this link and never stored. Leave it empty to print the link in the web server’s terminal instead."
            : "Used once to send this link and never stored. Leave it empty to use this server’s own key."
        }
      >
        {(props) => (
          <input
            {...props}
            name="resendApiKey"
            type="password"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="re_…"
            maxLength={200}
            onKeyDown={(event) => {
              // Enter sends the link (the form's own submit is the password sign-in); Escape
              // backs out of the step, like closing a dialog.
              if (event.key === "Enter") {
                event.preventDefault();
                onSend();
              } else if (event.key === "Escape") {
                event.preventDefault();
                onCancel();
              }
            }}
          />
        )}
      </Field>
      <div className="flex gap-2">
        <Button className="flex-1" loading={busy} disabled={disabled} onClick={onSend}>
          {busy ? "Sending…" : "Send sign-in link"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </fieldset>
  );
}

/** Google sign-in. Before it is configured (development only) it is shown disabled, with the
 * steps to set it up, so the option is not silently missing. */
function GoogleButton({
  setupNeeded,
  busy,
  disabled,
  onClick,
}: {
  setupNeeded: boolean;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const noteId = useId();
  return (
    <div>
      <Button
        variant="secondary"
        className="w-full"
        onClick={onClick}
        loading={busy}
        disabled={setupNeeded || disabled}
        aria-describedby={setupNeeded ? noteId : undefined}
      >
        Continue with Google
      </Button>
      {setupNeeded ? (
        <p id={noteId} className="mt-1 text-center text-xs text-muted">
          Not set up yet: add <code className="font-mono">GOOGLE_CLIENT_ID</code> and{" "}
          <code className="font-mono">GOOGLE_CLIENT_SECRET</code> to{" "}
          <code className="font-mono">frontend/.env.local</code> (SETUP_MANUAL.md, Part&nbsp;2).
        </p>
      ) : null}
    </div>
  );
}
