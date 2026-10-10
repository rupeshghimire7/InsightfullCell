import "server-only";
import { z } from "zod";

/**
 * Server configuration, validated once at first use (not at import time, so `next build` works
 * without secrets). Missing or malformed values fail fast with a message naming the variable.
 */
const schema = z
  .object({
    NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
    /** Deployment stage (same values as the API's ENVIRONMENT). NODE_ENV is Next.js's build mode. */
    APP_ENV: z.enum(["local", "test", "staging", "production"]).default("local"),

    /** Public URL of this web app. Also the issuer of the access tokens the API verifies. */
    BETTER_AUTH_URL: z.url(),
    BETTER_AUTH_SECRET: z.string().min(32, "Use at least 32 random characters"),
    /** Better Auth tables live in the `auth` schema (created by `pnpm auth:migrate`). */
    AUTH_DATABASE_URL: z.string().min(1),
    AUTH_MIGRATION_DATABASE_URL: z.string().min(1).optional(),
    AUTH_JWT_AUDIENCE: z.string().min(1).default("insightfulcell-api"),
    /** Audience of run-scoped event-stream tickets; must match the API's AUTH_EVENTS_AUDIENCE. */
    AUTH_EVENTS_AUDIENCE: z.string().min(1).default("insightfulcell-events"),
    /** Sign-in link requests and verifications allowed per IP per minute. Raise only for tests. */
    AUTH_MAGIC_LINK_RATE_MAX: z.coerce.number().int().min(1).max(1000).default(5),

    /** Base URL of the FastAPI service, reachable from the Next.js server only. */
    API_BASE_URL: z.url(),
    /**
     * Browser-facing URL of the same API. Browsers open live-progress streams on it directly (with
     * a run-scoped ticket), so web functions are never held open. Defaults to API_BASE_URL.
     */
    API_PUBLIC_URL: z.url().optional(),
    /** Browser-facing origin of object storage; allowed by the CSP `connect-src`. */
    S3_PUBLIC_ORIGIN: z.url(),

    GOOGLE_CLIENT_ID: z.string().min(1).optional(),
    GOOGLE_CLIENT_SECRET: z.string().min(1).optional(),

    /**
     * "Email me a sign-in link". Off until a verified mail domain is set up: the button is shown
     * disabled with a note, and the server does not offer the endpoint at all.
     */
    EMAIL_LINK_SIGN_IN: z.enum(["on", "off"]).default("off"),
    EMAIL_TRANSPORT: z.enum(["console", "resend"]).default("console"),
    EMAIL_FROM: z.string().min(3).default("InsightfulCell <onboarding@resend.dev>"),
    RESEND_API_KEY: z.string().min(1).optional(),
  })
  .superRefine((env, ctx) => {
    if (Boolean(env.GOOGLE_CLIENT_ID) !== Boolean(env.GOOGLE_CLIENT_SECRET)) {
      ctx.addIssue({
        code: "custom",
        path: ["GOOGLE_CLIENT_SECRET"],
        message: "Set both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or neither",
      });
    }
    if (env.EMAIL_TRANSPORT === "resend" && !env.RESEND_API_KEY) {
      ctx.addIssue({
        code: "custom",
        path: ["RESEND_API_KEY"],
        message: "Required when EMAIL_TRANSPORT=resend",
      });
    }
    if (env.AUTH_EVENTS_AUDIENCE === env.AUTH_JWT_AUDIENCE) {
      ctx.addIssue({
        code: "custom",
        path: ["AUTH_EVENTS_AUDIENCE"],
        message: "Must differ from AUTH_JWT_AUDIENCE, so a ticket can never act as an API token",
      });
    }
    if (env.APP_ENV === "production") {
      if (!(env.API_PUBLIC_URL ?? env.API_BASE_URL).startsWith("https://")) {
        ctx.addIssue({
          code: "custom",
          path: ["API_PUBLIC_URL"],
          message: "Must be https in production (browsers connect to it directly)",
        });
      }
      if (!env.BETTER_AUTH_URL.startsWith("https://")) {
        ctx.addIssue({
          code: "custom",
          path: ["BETTER_AUTH_URL"],
          message: "Must be https in production",
        });
      }
      if (env.EMAIL_LINK_SIGN_IN === "on" && env.EMAIL_TRANSPORT === "console") {
        ctx.addIssue({
          code: "custom",
          path: ["EMAIL_TRANSPORT"],
          message: "The console transport prints sign-in links and is not allowed in production",
        });
      }
    }
  });

export type Env = z.infer<typeof schema>;

let cached: Env | undefined;

export function parseEnv(source: Record<string, string | undefined>): Env {
  const result = schema.safeParse(source);
  if (!result.success) {
    const problems = result.error.issues
      .map((issue) => `  - ${issue.path.join(".") || "(root)"}: ${issue.message}`)
      .join("\n");
    throw new Error(`Invalid environment configuration:\n${problems}`);
  }
  return result.data;
}

export function getEnv(): Env {
  cached ??= parseEnv(process.env);
  return cached;
}

/** Where browsers reach the API (live progress only; everything else goes through the BFF). */
export function apiPublicUrl(env: Env = getEnv()): string {
  return (env.API_PUBLIC_URL ?? env.API_BASE_URL).replace(/\/+$/, "");
}
