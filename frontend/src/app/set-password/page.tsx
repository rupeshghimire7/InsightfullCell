import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { SetPasswordForm } from "@/components/auth/set-password-form";
import { SignOutLink } from "@/components/auth/sign-out-link";
import { Brand } from "@/components/ui/brand";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable, hasPassword } from "@/lib/session";

export const metadata: Metadata = { title: "Create a password" };

/**
 * `/set-password`: every signed-in person without a password chooses one here before using the
 * app (ADR-042); the signed-in layouts send them. Signed out: sign in. Has one already: home.
 */
export default async function SetPasswordPage() {
  await connection();
  const env = getEnv();
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return <ServiceUnavailable retryHref="/set-password" developerHint={env.APP_ENV === "local"} />;
  }
  if (!session) redirect("/sign-in");
  if (await hasPassword(session.userId)) redirect("/");

  return (
    <main id="main" className="flex min-h-dvh items-center justify-center px-4 py-12 sm:px-8">
      <div className="w-full max-w-[400px]">
        <p className="text-sm">
          <Brand size={28} />
        </p>
        <h1 className="mt-6 text-2xl font-semibold">Create a password</h1>
        <p className="mt-1 mb-8 text-muted">
          One last step for <strong className="font-semibold break-all">{session.email}</strong>:
          choose the password you’ll sign in with.
        </p>
        <SetPasswordForm email={session.email} />
        <p className="mt-6 text-center text-sm text-muted">
          Not you? <SignOutLink />
        </p>
      </div>
    </main>
  );
}
