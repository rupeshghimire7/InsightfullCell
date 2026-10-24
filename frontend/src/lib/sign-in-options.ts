import type { GoogleSignIn, LinkDelivery } from "@/components/auth/sign-in-form";
import type { Env } from "@/lib/env";

/** Which sign-in options the page offers, from configuration. Pure, so it is unit-tested. */
export function googleSignIn(env: Env): GoogleSignIn {
  if (env.GOOGLE_SIGN_IN !== "on") return "disabled";
  if (env.GOOGLE_CLIENT_ID) return "on";
  return env.APP_ENV === "production" ? "off" : "setup-needed";
}

/** Whether the app emails links and codes, prints them (local development), or neither. */
export function linkDelivery(env: Env): LinkDelivery {
  if (env.EMAIL_LINK_SIGN_IN !== "on") return "off";
  return env.EMAIL_TRANSPORT === "console" ? "log" : "email";
}
