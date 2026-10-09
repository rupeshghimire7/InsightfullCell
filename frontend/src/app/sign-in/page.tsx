import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { SignInForm } from "@/components/auth/sign-in-form";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { MAGIC_LINK_TTL_SECONDS } from "@/lib/auth-constants";
import { getEnv } from "@/lib/env";
import { googleSignIn, linkDelivery } from "@/lib/sign-in-options";
import { getSessionOrUnavailable } from "@/lib/session";

export const metadata: Metadata = { title: "Sign in" };

const LINK_ERRORS: Record<string, string> = {
  EXPIRED_TOKEN: "That sign-in link has expired. Request a new one below.",
  INVALID_TOKEN: "That sign-in link was already used or isn’t valid. Request a new one below.",
  access_denied: "Sign-in was cancelled. You can try again below.",
};

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; mode?: string }>;
}) {
  const env = getEnv();
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return <ServiceUnavailable retryHref="/sign-in" developerHint={env.APP_ENV === "local"} />;
  }
  if (session) redirect("/");
  const { error, mode: modeParam } = await searchParams;
  const mode = modeParam === "register" ? "register" : "sign-in";

  return (
    <main
      id="main"
      className="mx-auto flex min-h-dvh max-w-[400px] flex-col justify-center px-4 py-12"
    >
      <p className="text-sm font-semibold text-accent">InsightfulCell</p>
      <h1 className="mt-2 text-2xl font-semibold text-balance">
        {mode === "register" ? "Create an account" : "Sign in"}
      </h1>
      <p className="mt-1 mb-8 text-muted">Reports where every number shows its work.</p>
      <SignInForm
        key={mode}
        mode={mode}
        google={googleSignIn(env)}
        linkDelivery={linkDelivery(env)}
        initialError={
          error
            ? (LINK_ERRORS[error] ?? "We couldn’t sign you in. Request a new link below.")
            : undefined
        }
        expiresInMinutes={MAGIC_LINK_TTL_SECONDS / 60}
      />
    </main>
  );
}
