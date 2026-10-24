import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { ResetLinkExpired, ResetPasswordForm } from "@/components/auth/reset-password-form";
import { Brand } from "@/components/ui/brand";
import { getEnv } from "@/lib/env";

// The token is in the address: it must never leave in a Referer header.
export const metadata: Metadata = { title: "Choose a new password", referrer: "no-referrer" };

/**
 * `/sign-in/reset`: where an emailed reset link lands once Better Auth has checked its token
 * (`?token=…`), or found it expired or unknown (`?error=INVALID_TOKEN`). Signed-in people can use
 * it too: the reset signs every session out anyway.
 */
export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[]; error?: string | string[] }>;
}) {
  await connection();
  if (getEnv().PASSWORD_RESET !== "on") notFound();
  const { token, error } = await searchParams;
  const usable = typeof token === "string" && token.length > 0 && !error;

  return (
    <main id="main" className="flex min-h-dvh items-center justify-center px-4 py-12 sm:px-8">
      <div className="w-full max-w-[400px]">
        <p className="text-sm">
          <Brand size={28} />
        </p>
        <h1 className="mt-6 text-2xl font-semibold">Choose a new password</h1>
        <p className="mt-1 mb-8 text-muted">
          {usable ? "Type it twice. You’ll sign in with it from now on." : "Reset links work once."}
        </p>
        {usable ? <ResetPasswordForm token={token} /> : <ResetLinkExpired />}
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
