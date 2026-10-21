import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { EmailLinkForm } from "@/components/auth/email-link-form";
import { Brand } from "@/components/ui/brand";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { MAGIC_LINK_TTL_SECONDS } from "@/lib/auth-constants";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable } from "@/lib/session";
import { linkDelivery } from "@/lib/sign-in-options";

export const metadata: Metadata = { title: "Email me a sign-in link" };

/** `/sign-in/email`: asks for the address a sign-in link goes to (from sign-in and registration). */
export default async function EmailLinkPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  await connection();
  const env = getEnv();
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return (
      <ServiceUnavailable retryHref="/sign-in/email" developerHint={env.APP_ENV === "local"} />
    );
  }
  if (session) redirect("/");
  const delivery = linkDelivery(env);
  const back = (await searchParams).mode === "register" ? "/sign-in?mode=register" : "/sign-in";

  return (
    <main id="main" className="flex min-h-dvh items-center justify-center px-4 py-12 sm:px-8">
      <div className="w-full max-w-[400px]">
        <p className="text-sm">
          <Brand size={28} />
        </p>
        <h1 className="mt-6 text-2xl font-semibold">Email me a sign-in link</h1>
        <p className="mt-1 mb-8 text-muted">
          {delivery === "off"
            ? "Sign-in links aren’t available yet."
            : "We’ll send a link that signs you in, with no password. New here? It creates your account too."}
        </p>
        {delivery === "off" ? (
          <p>Sign in with your password or Google for now.</p>
        ) : (
          <EmailLinkForm delivery={delivery} expiresInMinutes={MAGIC_LINK_TTL_SECONDS / 60} />
        )}
        <p className="mt-6 text-center text-sm text-muted">
          <Link
            href={back}
            className="inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
          >
            Back to {back.includes("register") ? "registration" : "sign in"}
          </Link>
        </p>
      </div>
    </main>
  );
}
