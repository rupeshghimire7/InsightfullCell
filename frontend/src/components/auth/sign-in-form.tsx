"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui/button";
import { Eye, EyeSlash, Info } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-constants";

export type SignInMode = "sign-in" | "register";
type Action = "password" | "google";
type Status =
  { kind: "idle" } | { kind: "busy"; action: Action } | { kind: "error"; message: string };

type FieldName = "name" | "email" | "password" | "confirmPassword";
type FieldErrors = Partial<Record<FieldName, string>>;
interface Values {
  name: string;
  email: string;
  password: string;
  /** Registration only: the password typed again. Checked here, never sent. */
  confirmPassword: string;
}

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const TOO_MANY = "Too many attempts. Wait a minute, then try again.";
const UNREACHABLE = "We couldn’t reach the server. Check your connection, then try again.";
const SERVER_PROBLEM = "The server ran into a problem. Try again in a moment.";
const ORIGIN_REFUSED =
  "Sign-in isn’t allowed from this web address. Open the app at its usual address, or ask whoever runs it to allow this one.";

/**
 * Where sign-in links go. `log`: printed in the web server's terminal, never emailed.
 * `resend-test`: emailed from Resend's shared test sender, which only delivers to the Resend
 * account owner. `email`: emailed from a verified domain, to anyone. `off`: sign-in links are
 * switched off (the button is shown disabled, with a note saying so).
 */
export type LinkDelivery = "log" | "resend-test" | "email" | "off";
/**
 * `setup-needed`: shown disabled with setup steps (development only). `disabled`: switched off
 * (`GOOGLE_SIGN_IN=off`), shown disabled with a note saying so. `off`: not shown.
 */
export type GoogleSignIn = "on" | "setup-needed" | "disabled" | "off";

export interface SignInFormProps {
  mode?: SignInMode;
  google: GoogleSignIn;
  linkDelivery: LinkDelivery;
  /** Message from a failed link verification, shown above the form. */
  initialError?: string;
}

/** What is wrong with the fields an action needs, in field order. Empty when all are valid. */
export function validate(mode: SignInMode, action: "password", values: Values) {
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
    if (mode === "register" && !values.confirmPassword) {
      errors.confirmPassword = "Type your password again.";
    } else if (mode === "register" && values.confirmPassword !== values.password) {
      errors.confirmPassword = "The passwords don’t match.";
    }
  }
  return errors;
}

export interface AuthError {
  status?: number;
  code?: string;
}

/** Failures that are not about what was typed: the connection, this address, the server. */
export function transportError(error: AuthError): string | null {
  if (!error.status) return UNREACHABLE;
  if (error.status === 429) return TOO_MANY;
  if (error.code === "INVALID_ORIGIN" || error.code === "MISSING_OR_NULL_ORIGIN") {
    return ORIGIN_REFUSED;
  }
  if (error.status >= 500) return SERVER_PROBLEM;
  return null;
}

/** Runs an auth client call; a request that never got an answer becomes `{ status: 0 }`. */
export async function settle(
  call: () => Promise<{ error: AuthError | null }>,
): Promise<AuthError | null> {
  try {
    return (await call()).error;
  } catch {
    return { status: 0 };
  }
}

const subscribeNothing = () => () => {};

/** False in the server-rendered HTML, true once React runs in the browser. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribeNothing,
    () => true,
    () => false,
  );
}

export function passwordError(mode: SignInMode, error: AuthError): string {
  const transport = transportError(error);
  if (transport) return transport;
  if (mode === "register") {
    return error.code?.startsWith("USER_ALREADY_EXISTS")
      ? "An account with this email already exists. Sign in instead."
      : "We couldn’t create your account. Try again.";
  }
  return error.status === 401
    ? "That email and password don’t match. If you signed up with an emailed link or Google, use that instead."
    : "We couldn’t sign you in. Try again.";
}

export function SignInForm({
  mode = "sign-in",
  google,
  linkDelivery,
  initialError,
}: SignInFormProps) {
  const router = useRouter();
  const hydrated = useHydrated();
  const formRef = useRef<HTMLFormElement>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const registering = mode === "register";

  /** Reads the form, shows any field errors and focuses the first; `null` when invalid. */
  function readValid(action: "password"): Values | null {
    const data = new FormData(formRef.current ?? undefined);
    const values: Values = {
      name: (data.get("name") ?? "").toString().trim(),
      email: (data.get("email") ?? "").toString().trim(),
      password: (data.get("password") ?? "").toString(),
      confirmPassword: (data.get("confirmPassword") ?? "").toString(),
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
    const error = await settle(() =>
      registering
        ? authClient.signUp.email({
            name: values.name,
            email: values.email,
            password: values.password,
          })
        : authClient.signIn.email({ email: values.email, password: values.password }),
    );
    if (error) {
      setStatus({ kind: "error", message: passwordError(mode, error) });
      return;
    }
    enterApp();
  }

  async function onGoogle() {
    setStatus({ kind: "busy", action: "google" });
    const error = await settle(() =>
      authClient.signIn.social({ provider: "google", callbackURL: "/" }),
    );
    if (error) {
      setStatus({
        kind: "error",
        message:
          transportError(error) ?? "Google sign-in didn’t complete. Try again, or use your email.",
      });
    }
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

      {hydrated ? null : (
        // Shown only if the page's scripts never start (CSS reveals it after a delay), so a sign-in
        // that cannot work says so instead of doing nothing.
        <p role="status" className="reveal-late text-sm text-muted">
          The page is still loading. If this message stays, reload the page; if it keeps happening,
          the server isn’t allowing this web address yet.
        </p>
      )}
      {/* POST, so that even without scripts the password never ends up in the address bar. */}
      <form ref={formRef} onSubmit={onSubmit} method="post" noValidate className="space-y-4">
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
            <PasswordInput
              {...props}
              name="password"
              autoComplete={registering ? "new-password" : "current-password"}
            />
          )}
        </Field>
        {registering ? (
          <Field label="Confirm password" error={fieldErrors.confirmPassword}>
            {(props) => (
              <PasswordInput {...props} name="confirmPassword" autoComplete="new-password" />
            )}
          </Field>
        ) : null}
        <Button type="submit" loading={busy === "password"} disabled={!hydrated} className="w-full">
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
        <div className="space-y-4 pt-1">
          <div className="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>
          {google !== "off" ? (
            <GoogleButton
              state={google}
              busy={busy === "google"}
              disabled={busy !== null && busy !== "google"}
              onClick={onGoogle}
            />
          ) : null}
          <MagicLinkButton delivery={linkDelivery} mode={mode} googleOn={google === "on"} />
        </div>
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

export interface FieldInputProps {
  id: string;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
  className: string;
}

/**
 * A password input with an eye button that shows or hides what was typed. The button comes after
 * the input in tab order, names what it will do ("Show password" / "Hide password") and never
 * submits the form.
 */
export function PasswordInput({
  className,
  ...props
}: FieldInputProps & { name: string; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  const Icon = visible ? EyeSlash : Eye;
  return (
    <div className="relative">
      <input
        {...props}
        type={visible ? "text" : "password"}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        maxLength={PASSWORD_MAX_LENGTH}
        className={`${className} pr-11`}
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-controls={props.id}
        className="absolute top-1/2 right-1 grid size-8 -translate-y-1/2 place-items-center rounded-control text-muted hover:bg-bg hover:text-text pointer-coarse:size-9"
      >
        <Icon aria-hidden="true" className="size-4.5" />
      </button>
    </div>
  );
}

/** A labelled input with an optional hint and an error the input is described by. */
export function Field({
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
 * "Email me a sign-in link": opens the page that asks for the address (`/sign-in/email`). While
 * links cannot reach everyone (printed to the server log, or sent from Resend's test sender), a
 * "Testing only" note says so, shown on hover of the link or its info icon; Escape dismisses it
 * (WCAG 1.4.13); screen readers read it through `aria-describedby`. Switched off, it is a button
 * that stays visible but inert (`aria-disabled`), and the same note says links are not available.
 */
function MagicLinkButton({
  delivery,
  mode,
  googleOn,
}: {
  delivery: LinkDelivery;
  mode: SignInMode;
  googleOn: boolean;
}) {
  const noteId = useId();
  const testing = delivery !== "email";
  const off = delivery === "off";
  const [hovered, setHovered] = useState(false);
  const [dismissed, setDismissed] = useState(false);
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

  const control = off ? (
    <Button variant="secondary" className="w-full" aria-disabled={true} aria-describedby={noteId}>
      Email me a sign-in link
    </Button>
  ) : (
    <Link
      href={mode === "register" ? "/sign-in/email?mode=register" : "/sign-in/email"}
      aria-describedby={testing ? noteId : undefined}
      className="press inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium whitespace-nowrap text-text hover:bg-bg pointer-coarse:min-h-11"
    >
      Email me a sign-in link
    </Link>
  );
  if (!testing) return control;

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
        {control}
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
              {googleOn ? " password or Google." : " password."}
            </p>
          ) : delivery === "log" ? (
            <p className="mt-1">
              No email is sent yet: the link is printed in the terminal running the web app.
            </p>
          ) : (
            <p className="mt-1">
              Email sign-in is for testing this demo: it only works with the project owner’s email
              until an organisation’s email domain is set up. Everyone else, please register with a
              password.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** Google sign-in. Switched off, or before it is configured (development only), it is shown
 * disabled with a note (or the steps to set it up), so the option is not silently missing. */
function GoogleButton({
  state,
  busy,
  disabled,
  onClick,
}: {
  state: Exclude<GoogleSignIn, "off">;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const noteId = useId();
  const unavailable = state !== "on";
  return (
    <div>
      <Button
        variant="secondary"
        className="w-full"
        onClick={onClick}
        loading={busy}
        disabled={unavailable || disabled}
        aria-describedby={unavailable ? noteId : undefined}
      >
        Continue with Google
      </Button>
      {state === "disabled" ? (
        <p id={noteId} className="mt-1 text-center text-xs text-muted">
          Google sign-in isn’t available yet. Sign in with your password.
        </p>
      ) : state === "setup-needed" ? (
        <p id={noteId} className="mt-1 text-center text-xs text-muted">
          Not set up yet: add <code className="font-mono">GOOGLE_CLIENT_ID</code> and{" "}
          <code className="font-mono">GOOGLE_CLIENT_SECRET</code> to{" "}
          <code className="font-mono">frontend/.env.local</code> (SETUP_MANUAL.md, Part&nbsp;2).
        </p>
      ) : null}
    </div>
  );
}
