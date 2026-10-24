import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { connection } from "next/server";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { Brand } from "@/components/ui/brand";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { PASSWORD_RESET_TTL_SECONDS } from "@/lib/auth-constants";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable } from "@/lib/session";

export const metadata: Metadata = { title: "Forgot password" };

/** `/sign-in/forgot`: asks for the address a password-reset link goes to (ADR-041). */
export default async function ForgotPasswordPage() {
  await connection();
  const env = getEnv();
  if (env.PASSWORD_RESET !== "on") notFound();
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return (
      <ServiceUnavailable retryHref="/sign-in/forgot" developerHint={env.APP_ENV === "local"} />
    );
  }
  if (session) redirect("/");

  return (
    <main id="main" className="flex min-h-dvh items-center justify-center px-4 py-12 sm:px-8">
      <div className="w-full max-w-[400px]">
        <p className="text-sm">
          <Brand size={28} />
        </p>
        <h1 className="mt-6 text-2xl font-semibold">Forgot your password?</h1>
        <p className="mt-1 mb-8 text-muted">
          Enter the email you sign in with, and we’ll send a link to choose a new password.
        </p>
        <ForgotPasswordForm
          expiresInMinutes={PASSWORD_RESET_TTL_SECONDS / 60}
          toLog={env.EMAIL_TRANSPORT === "console"}
        />
        <p className="mt-6 text-center text-sm text-muted">
          <Link
            href="/sign-in"
            className="inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
          >
            Back to sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
