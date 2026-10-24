"use client";

import type { BetterAuthClientPlugin } from "better-auth/client";
import { emailOTPClient, magicLinkClient, organizationClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";
import type { registration } from "@/lib/registration";

/** Typed client for `/register/email` and `/email-code/send` (`authClient.register.email`…). */
const registrationClient = () =>
  ({
    id: "registration",
    $InferServerPlugin: {} as ReturnType<typeof registration>,
  }) satisfies BetterAuthClientPlugin;

export const authClient = createAuthClient({
  plugins: [magicLinkClient(), emailOTPClient(), registrationClient(), organizationClient()],
});
