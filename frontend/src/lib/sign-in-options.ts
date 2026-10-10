import type { GoogleSignIn, LinkDelivery } from "@/components/auth/sign-in-form";
import type { Env } from "@/lib/env";

/** Which sign-in options the page offers, from configuration. Pure, so it is unit-tested. */
export function googleSignIn(env: Env): GoogleSignIn {
  if (env.GOOGLE_CLIENT_ID) return "on";
  return env.APP_ENV === "production" ? "off" : "setup-needed";
}

/** Resend's shared `@resend.dev` sender only delivers to the Resend account owner. */
export function linkDelivery(env: Env): LinkDelivery {
  if (env.EMAIL_LINK_SIGN_IN !== "on") return "off";
  if (env.EMAIL_TRANSPORT === "console") return "log";
  return /@resend\.dev>?\s*$/i.test(env.EMAIL_FROM) ? "resend-test" : "email";
}
