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
  type InputHTMLAttributes,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui/button";
import { Eye, EyeSlash } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";
import {
  EMAIL_ALREADY_REGISTERED_MESSAGE,
  EMAIL_NOT_ALLOWED_MESSAGE,
  OTP_LENGTH,
  OTP_RESEND_COOLDOWN_SECONDS,
  OTP_TTL_SECONDS,
  PASSWORD_MAX_LENGTH,
} from "@/lib/auth-constants";

export type SignInMode = "sign-in" | "register";
type Action = "password" | "code" | "google";
type Status =
  | { kind: "idle" }
  | { kind: "busy"; action: Action }
  | { kind: "error"; message: string; action?: ReactNode };

type FieldName = "name" | "email" | "password";
type FieldErrors = Partial<Record<FieldName, string>>;
interface Values {
  name: string;
  email: string;
  password: string;
}

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const TOO_MANY = "Too many attempts. Wait a minute, then try again.";
const UNREACHABLE = "We couldn’t reach the server. Check your connection, then try again.";
const SERVER_PROBLEM = "The server ran into a problem. Try again in a moment.";
const ORIGIN_REFUSED =
  "Sign-in isn’t allowed from this web address. Open the app at its usual address, or ask whoever runs it to allow this one.";

const linkClass =
  "inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11";
const actionLinkClass =
  "inline-flex min-h-9 items-center rounded-control bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110";

/**
 * Where emails (sign-in links, registration codes) go. `log`: printed in the web server's
 * terminal, never emailed (local development). `email`: emailed. `off`: no email at all: links
 * are switched off and registration is by Google only.
 */
export type LinkDelivery = "log" | "email" | "off";
/**
 * `setup-needed`: shown disabled with setup steps (development only). `disabled`: switched off
 * (`GOOGLE_SIGN_IN=off`), shown disabled. `off`: not shown.
 */
export type GoogleSignIn = "on" | "setup-needed" | "disabled" | "off";

export interface SignInFormProps {
  mode?: SignInMode;
  google: GoogleSignIn;
  linkDelivery: LinkDelivery;
  /** "Forgot password?" under the password, when signing in only (whenever email is on). */
  passwordReset?: boolean;
  /** Message from a failed link verification or Google sign-in, shown above the form. */
  initialError?: string;
}

/** What is wrong with the fields the form needs, in field order. Empty when all are valid. */
export function validate(mode: SignInMode, values: Values) {
  const errors: FieldErrors = {};
  if (mode === "register" && !values.name) errors.name = "Enter your name.";
  if (!EMAIL_PATTERN.test(values.email)) {
    errors.email = "Enter your email address, like name@company.com.";
  }
  if (mode === "sign-in" && !values.password) errors.password = "Enter your password.";
  return errors;
}

export interface AuthError {
  status?: number;
  code?: string;
  message?: string;
}

/** Failures that are not about what was typed: the connection, this address, the server. */
export function transportError(error: AuthError): string | null {
  if (!error.status) return UNREACHABLE;
  if (error.status === 429 && error.code !== "CODE_COOLDOWN") return TOO_MANY;
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

/** Why a password sign-in failed. Unknown email or wrong password: one answer for both. */
export function passwordError(error: AuthError): string {
  return (
    transportError(error) ??
    (error.status === 401
      ? "That email and password don’t match. If you signed up with an emailed link or Google, use that instead."
      : "We couldn’t sign you in. Try again.")
  );
}

/** Why the registration code was not sent, from the server's error code (`registration.ts`). */
export function registerError(error: AuthError): string {
  switch (error.code) {
    case "EMAIL_ALREADY_REGISTERED":
      return EMAIL_ALREADY_REGISTERED_MESSAGE;
    case "EMAIL_NOT_ALLOWED":
      return EMAIL_NOT_ALLOWED_MESSAGE;
    case "CODE_COOLDOWN":
      return error.message ?? "We just sent a code. Wait a minute before asking for another.";
    case "EMAIL_NOT_CONFIGURED":
      return "Email isn’t set up correctly on this server. Sign up with Google instead.";
    case "EMAIL_RATE_LIMITED":
      return "The email provider is busy. Wait a minute, then try again.";
    case "RESEND_RECIPIENT_NOT_ALLOWED":
    case "EMAIL_DELIVERY_FAILED":
      return "We couldn’t send the code. Try again in a moment.";
  }
  return transportError(error) ?? "We couldn’t create your account. Try again.";
}

/** Why a code was refused (emailOTP plugin error codes). */
export function codeError(error: AuthError): string {
  switch (error.code) {
    case "INVALID_OTP":
      return "That code isn’t right. Check the email and try again.";
    case "OTP_EXPIRED":
      return "That code has expired. Send a new one.";
    case "TOO_MANY_ATTEMPTS":
      return "Too many wrong codes. Send a new one.";
  }
  return transportError(error) ?? "We couldn’t check the code. Try again.";
}

/** Who the code step is for: a new registration, or an older account verifying its address. */
type CodeStep = { email: string; purpose: "register" | "verify" };

export function SignInForm({
  mode = "sign-in",
  google,
  linkDelivery,
  passwordReset = false,
  initialError,
}: SignInFormProps) {
  const router = useRouter();
  const hydrated = useHydrated();
  const formRef = useRef<HTMLFormElement>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [codeStep, setCodeStep] = useState<CodeStep | null>(null);
  const registering = mode === "register";
  const registerByEmail = linkDelivery !== "off";

  /** Reads the form, shows any field errors and focuses the first; `null` when invalid. */
  function readValid(): Values | null {
    const data = new FormData(formRef.current ?? undefined);
    const values: Values = {
      name: (data.get("name") ?? "").toString().trim(),
      email: (data.get("email") ?? "").toString().trim(),
      password: (data.get("password") ?? "").toString(),
    };
    const errors = validate(mode, values);
    setFieldErrors(errors);
    const first = Object.keys(errors)[0];
    if (first) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${first}"]`)?.focus();
      return null;
    }
    return values;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const values = readValid();
    if (!values) return;
    if (registering) {
      setStatus({ kind: "busy", action: "code" });
      const error = await settle(() =>
        authClient.register.email({ name: values.name, email: values.email }),
      );
      if (!error) {
        setStatus({ kind: "idle" });
        setCodeStep({ email: values.email, purpose: "register" });
        return;
      }
      setStatus({
        kind: "error",
        message: registerError(error),
        action:
          error.code === "EMAIL_ALREADY_REGISTERED" ? (
            <Link href="/sign-in" className={actionLinkClass}>
              Sign in
            </Link>
          ) : undefined,
      });
      return;
    }
    setStatus({ kind: "busy", action: "password" });
    const error = await settle(() =>
      authClient.signIn.email({ email: values.email, password: values.password }),
    );
    if (!error) {
      router.replace("/");
      router.refresh();
      return;
    }
    // The password was right, but the address was never verified (an account registered with a
    // password before ADR-042): the server has just emailed a code.
    if (error.code === "EMAIL_NOT_VERIFIED") {
      setStatus({ kind: "idle" });
      setCodeStep({ email: values.email, purpose: "verify" });
      return;
    }
    setStatus({ kind: "error", message: passwordError(error) });
  }

  async function onGoogle() {
    setStatus({ kind: "busy", action: "google" });
    const error = await settle(() =>
      authClient.signIn.social(
        registering
          ? {
              provider: "google",
              callbackURL: "/",
              errorCallbackURL: "/sign-in?mode=register",
              // Only this button may create an account (`disableImplicitSignUp` on the server).
              requestSignUp: true,
            }
          : { provider: "google", callbackURL: "/", errorCallbackURL: "/sign-in" },
      ),
    );
    if (error) {
      setStatus({
        kind: "error",
        message:
          transportError(error) ?? "Google sign-in didn’t complete. Try again, or use your email.",
      });
    }
  }

  if (codeStep) {
    return (
      <EmailCodeStep
        {...codeStep}
        delivery={linkDelivery}
        onDone={() => {
          // A new account has no password yet; the signed-in area would send it there anyway.
          router.replace(codeStep.purpose === "register" ? "/set-password" : "/");
          router.refresh();
        }}
        onBack={() => setCodeStep(null)}
      />
    );
  }

  const busy = status.kind === "busy" ? status.action : null;
  const googleButton =
    google !== "off" ? (
      <GoogleButton
        state={google}
        label={registering ? "Sign up with Google" : "Continue with Google"}
        busy={busy === "google"}
        disabled={busy !== null && busy !== "google"}
        onClick={onGoogle}
      />
    ) : null;
  const switchMode = (
    <p className="text-center text-sm text-muted">
      {registering ? "Already have an account? " : "New to InsightfulCell? "}
      <Link href={registering ? "/sign-in" : "/sign-in?mode=register"} className={linkClass}>
        {registering ? "Sign in" : "Create an account"}
      </Link>
    </p>
  );
  const alerts = (
    <>
      {initialError ? (
        <ProblemAlert
          title={registering ? "Registration didn’t work" : "Sign-in problem"}
          detail={initialError}
        />
      ) : null}
      {status.kind === "error" ? (
        <ProblemAlert
          title={registering ? "Registration didn’t work" : "Sign-in didn’t work"}
          detail={status.message}
          action={status.action}
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
    </>
  );

  // Registration without email delivery: Google is the only way to create an account.
  if (registering && !registerByEmail) {
    return (
      <div className="space-y-5">
        {alerts}
        <p className="text-sm">
          {google === "off"
            ? "Registration isn’t available right now."
            : "Registering with an email code isn’t available yet. Sign up with Google instead."}
        </p>
        {googleButton}
        {switchMode}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {alerts}
      {/* POST, so that even without scripts the password never ends up in the address bar. */}
      <form ref={formRef} onSubmit={onSubmit} method="post" noValidate className="space-y-4">
        {registering ? (
          <Field label="Name" error={fieldErrors.name}>
            {(props) => (
              <input {...props} name="name" type="text" autoComplete="name" maxLength={100} />
            )}
          </Field>
        ) : null}
        <Field
          label="Email address"
          error={fieldErrors.email}
          hint={registering ? "We’ll email you a code to confirm it’s yours." : undefined}
        >
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
        {registering ? null : (
          <>
            <Field label="Password" error={fieldErrors.password}>
              {(props) => (
                <PasswordInput {...props} name="password" autoComplete="current-password" />
              )}
            </Field>
            {passwordReset ? (
              <p className="-mt-2 text-right text-sm">
                <Link href="/sign-in/forgot" className={linkClass}>
                  Forgot password?
                </Link>
              </p>
            ) : null}
          </>
        )}
        <Button
          type="submit"
          loading={busy === "password" || busy === "code"}
          disabled={!hydrated}
          className="w-full"
        >
          {registering
            ? busy === "code"
              ? "Sending code…"
              : "Send me a code"
            : busy === "password"
              ? "Signing in…"
              : "Sign in"}
        </Button>

        {/* The other ways in, after "or": Google, then (signing in only) the sign-in link, which
            opens its own page, `/sign-in/email`. */}
        {googleButton || !registering ? (
          <div className="space-y-4 pt-1">
            <div className="flex items-center gap-3 text-xs text-muted" aria-hidden="true">
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>
            {googleButton}
            {registering ? null : <MagicLinkButton delivery={linkDelivery} />}
          </div>
        ) : null}
      </form>
      {switchMode}
    </div>
  );
}

/**
 * The 6-digit code step: after registering, or when an older account signs in with a password
 * before its address was verified. A correct code verifies the address and signs in. "Send a new
 * code" waits out the cooldown, counting down in its label.
 */
export function EmailCodeStep({
  email,
  purpose,
  delivery,
  onDone,
  onBack,
}: CodeStep & { delivery: LinkDelivery; onDone: () => void; onBack: () => void }) {
  const hydrated = useHydrated();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"verify" | "resend" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [wait, setWait] = useState(OTP_RESEND_COOLDOWN_SECONDS);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const otp = (inputRef.current?.value ?? "").replace(/\s+/g, "");
    if (!new RegExp(`^\\d{${OTP_LENGTH}}$`).test(otp)) {
      setError(`Enter the ${OTP_LENGTH}-digit code from the email.`);
      inputRef.current?.focus();
      return;
    }
    setBusy("verify");
    setError(null);
    const failure = await settle(() => authClient.emailOtp.verifyEmail({ email, otp }));
    if (!failure) {
      onDone();
      return;
    }
    setBusy(null);
    setNotice(null);
    setError(codeError(failure));
    inputRef.current?.select();
  }

  async function onResend() {
    if (busy || wait > 0) return;
    setBusy("resend");
    const failure = await settle(() => authClient.emailCode.send({ email }));
    setBusy(null);
    if (failure) {
      setNotice(null);
      setError(registerError(failure));
      return;
    }
    setError(null);
    setNotice("We sent a new code. Only the newest code works.");
    setWait(OTP_RESEND_COOLDOWN_SECONDS);
    if (inputRef.current) inputRef.current.value = "";
    inputRef.current?.focus();
  }

  const toLog = delivery === "log";
  return (
    <div className="space-y-5">
      <div role="status" className="space-y-2">
        <h2 className="text-xl font-semibold">
          {toLog ? "Check the server terminal" : "Check your email"}
        </h2>
        <p>
          {toLog ? "Testing mode: no email was sent. The code" : "We sent a 6-digit code"} for{" "}
          <strong className="font-semibold break-all">{email}</strong>
          {toLog ? " is printed in the terminal running the web app." : "."}{" "}
          {purpose === "register"
            ? "Enter it to confirm the address and create your account."
            : "Enter it to confirm your address, then you’re signed in."}{" "}
          It expires in {OTP_TTL_SECONDS / 60}&nbsp;minutes.
        </p>
        {notice ? <p className="text-sm text-muted">{notice}</p> : null}
      </div>
      <form onSubmit={onSubmit} method="post" noValidate className="space-y-4">
        <div>
          <label htmlFor={inputId} className="block text-sm font-medium">
            6-digit code
          </label>
          <input
            ref={inputRef}
            id={inputId}
            name="otp"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={OTP_LENGTH + 2}
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className="mt-1.5 block min-h-10 w-full rounded-control border border-border bg-surface px-3 font-mono text-lg tracking-[0.3em] transition-colors hover:border-muted/60 focus-visible:border-accent aria-invalid:border-danger pointer-coarse:min-h-11"
          />
          {error ? (
            <p id={errorId} role="alert" className="mt-1 text-sm text-danger">
              {error}
            </p>
          ) : null}
        </div>
        <Button type="submit" loading={busy === "verify"} disabled={!hydrated} className="w-full">
          {busy === "verify" ? "Checking…" : "Verify"}
        </Button>
      </form>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <Button
          variant="secondary"
          onClick={onResend}
          loading={busy === "resend"}
          disabled={!hydrated || wait > 0}
        >
          {wait > 0 ? `Send a new code (${wait}s)` : "Send a new code"}
        </Button>
        <button type="button" onClick={onBack} className={linkClass}>
          Use a different email
        </button>
      </div>
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
}: FieldInputProps & {
  name: string;
  autoComplete: string;
} & Pick<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
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

/**
 * A labelled input with an optional hint and an error the input is described by. `describedBy`
 * adds the id of something rendered elsewhere (a password checklist).
 */
export function Field({
  label,
  error,
  hint,
  describedBy: extra,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  describedBy?: string;
  children: (props: FieldInputProps) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, extra ?? null, error ? errorId : null]
    .filter(Boolean)
    .join(" ");
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
 * "Email me a sign-in link": opens the page that asks for the address (`/sign-in/email`). Switched
 * off, it is a button that stays visible but inert (`aria-disabled`).
 */
function MagicLinkButton({ delivery }: { delivery: LinkDelivery }) {
  if (delivery === "off") {
    return (
      <Button variant="secondary" className="w-full" aria-disabled={true}>
        Email me a sign-in link
      </Button>
    );
  }
  return (
    <Link
      href="/sign-in/email"
      className="press inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium whitespace-nowrap text-text hover:bg-bg pointer-coarse:min-h-11"
    >
      Email me a sign-in link
    </Link>
  );
}

/** Google sign-in (or sign-up). Switched off it is shown disabled; before it is configured
 * (development only), disabled with the steps to set it up. */
function GoogleButton({
  state,
  label,
  busy,
  disabled,
  onClick,
}: {
  state: Exclude<GoogleSignIn, "off">;
  label: string;
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
        disabled={state !== "on" || disabled}
        aria-describedby={state === "setup-needed" ? noteId : undefined}
      >
        {label}
      </Button>
      {state === "setup-needed" ? (
        <p id={noteId} className="mt-1 text-center text-xs text-muted">
          Not set up yet: add <code className="font-mono">GOOGLE_CLIENT_ID</code> and{" "}
          <code className="font-mono">GOOGLE_CLIENT_SECRET</code> to{" "}
          <code className="font-mono">frontend/.env.local</code> (SETUP_MANUAL.md, Part&nbsp;2).
        </p>
      ) : null}
    </div>
  );
}
