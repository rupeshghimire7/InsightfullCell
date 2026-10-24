import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { SignInNotice } from "@/components/auth/sign-in-notice";
import { SignInForm } from "@/components/auth/sign-in-form";
import { Brand } from "@/components/ui/brand";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { getEnv, passwordResetOn } from "@/lib/env";
import { googleSignIn, linkDelivery } from "@/lib/sign-in-options";
import { getSessionOrUnavailable } from "@/lib/session";

export const metadata: Metadata = { title: "Sign in" };

const TAGLINE = "Your one stop solution for data analysis.";

/** `?error=` from a sign-in link or from Google (Better Auth's OAuth callback codes). */
const LINK_ERRORS: Record<string, string> = {
  EXPIRED_TOKEN: "That sign-in link has expired. Request a new one with “Email me a sign-in link”.",
  INVALID_TOKEN:
    "That sign-in link was already used or isn’t valid. Request a new one with “Email me a sign-in link”.",
  new_user_signup_disabled: "No account found for that email. Register first.",
  access_denied: "Sign-in was cancelled. You can try again below.",
  // Google on the sign-in page never creates an account (ADR-042).
  signup_disabled:
    "No InsightfulCell account uses that Google address. Register first, with “Sign up with Google”.",
  account_not_linked:
    "That email already has an InsightfulCell account whose address isn’t verified yet. Sign in with its password, or register it again to get a code, then use Google.",
  state_mismatch: "Google sign-in took too long or was opened in another tab. Try again.",
  please_restart_the_process:
    "Google sign-in took too long or was opened in another tab. Try again.",
};

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; mode?: string; account?: string; reset?: string }>;
}) {
  // Rendered per request: configuration is read when the server runs, never at build time
  // (the image is built without it).
  await connection();
  const env = getEnv();
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return <ServiceUnavailable retryHref="/sign-in" developerHint={env.APP_ENV === "local"} />;
  }
  if (session) redirect("/");
  const { error, mode: modeParam, account, reset } = await searchParams;
  const mode = modeParam === "register" ? "register" : "sign-in";

  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-[400px]">
          <div className="lg:hidden">
            <p className="text-sm">
              <Brand size={28} />
            </p>
            <p className="mt-1 text-sm text-muted">{TAGLINE}</p>
          </div>
          <h1 className="mt-6 text-2xl font-semibold lg:mt-0">
            {mode === "register" ? "Create an account" : "Sign in"}
          </h1>
          <p className="mt-1 mb-8 text-muted">
            {mode === "register"
              ? "Your workspace is created with your account."
              : "Welcome back. Sign in to your workspace."}
          </p>
          {account === "deleted" ? (
            <SignInNotice param="account">
              Your account and everything in it were deleted.
            </SignInNotice>
          ) : reset === "done" ? (
            <SignInNotice param="reset">
              Password changed. Sign in with your new password; every device that was signed in has
              been signed out.
            </SignInNotice>
          ) : null}
          <SignInForm
            key={mode}
            mode={mode}
            google={googleSignIn(env)}
            linkDelivery={linkDelivery(env)}
            passwordReset={passwordResetOn(env)}
            initialError={
              error
                ? (LINK_ERRORS[error] ??
                  "We couldn’t sign you in. Try again, or use another way in.")
                : undefined
            }
          />
        </div>
      </div>

      {/* After the form in reading order; shown first (left) on wide screens. */}
      <section
        aria-labelledby="promise-title"
        className="relative hidden flex-col justify-between overflow-hidden bg-brand-panel p-12 text-brand-panel-text lg:order-first lg:flex xl:p-16"
      >
        <p className="text-lg">
          <Brand size={44} />
        </p>
        <div className="max-w-lg space-y-5">
          <h2
            id="promise-title"
            className="text-4xl leading-[1.1] font-semibold tracking-tight text-balance xl:text-5xl"
          >
            {TAGLINE}
          </h2>
          <p className="max-w-[40ch] text-lg leading-relaxed text-pretty opacity-90">
            Bring a spreadsheet. Leave with clear answers, charts and a report your team can check.
          </p>
        </div>
        {/* One line, by design: short enough for the narrowest panel (1024 px wide screens). */}
        <p className="text-xs whitespace-nowrap opacity-85 xl:text-sm">
          We respect your data privacy. Raw rows never reach an AI model.
        </p>
      </section>
    </main>
  );
}
