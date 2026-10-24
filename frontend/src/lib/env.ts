import "server-only";
import { z } from "zod";
import { isLoopbackHost } from "@/lib/relay";

/**
 * Server configuration, validated once at first use (not at import time, so `next build` works
 * without secrets). Missing or malformed values fail fast with a message naming the variable.
 */
/** An optional value that compose may pass as an empty string: blank means unset. */
const optional = (inner: z.ZodString) =>
  z.preprocess(
    (value) => (typeof value === "string" && !value.trim() ? undefined : value),
    inner.optional(),
  );

const EMAIL_PATTERN = /^[^\s@,]+@[^\s@,]+\.[^\s@,]+$/;
/** Resend's shared `@resend.dev` sender, which only delivers to the Resend account owner. */
const RESEND_TEST_SENDER = /@resend\.dev>?\s*$/i;

const schema = z
  .object({
    NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
    /** Deployment stage (same values as the API's ENVIRONMENT). NODE_ENV is Next.js's build mode. */
    APP_ENV: z.enum(["local", "test", "staging", "production"]).default("local"),

    /** Public URL of this web app. Also the issuer of the access tokens the API verifies. */
    BETTER_AUTH_URL: z.url(),
    /**
     * Other origins the same app is opened at (a tunnel such as ngrok, a LAN address, a second
     * domain), comma separated. Sign-in and the backend proxy accept requests from them; tokens are
     * still issued as BETTER_AUTH_URL, so the API's AUTH_ISSUER does not change (ADR-038).
     */
    AUTH_TRUSTED_ORIGINS: z.string().default(""),
    BETTER_AUTH_SECRET: z.string().min(32, "Use at least 32 random characters"),
    /** Better Auth tables live in the `auth` schema (created by `pnpm auth:migrate`). */
    AUTH_DATABASE_URL: z.string().min(1),
    AUTH_MIGRATION_DATABASE_URL: z.string().min(1).optional(),
    AUTH_JWT_AUDIENCE: z.string().min(1).default("insightfulcell-api"),
    /** Audience of run-scoped event-stream tickets; must match the API's AUTH_EVENTS_AUDIENCE. */
    AUTH_EVENTS_AUDIENCE: z.string().min(1).default("insightfulcell-events"),
    /**
     * Sign-in link and registration-code requests, and code checks, allowed per IP per minute
     * (each endpoint counted apart). Raise only for tests.
     */
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

    /**
     * "Continue with Google". Off: the button is shown disabled with a note, and the server does
     * not offer Google at all, even when the credentials below are set.
     */
    GOOGLE_SIGN_IN: z.enum(["on", "off"]).default("off"),
    GOOGLE_CLIENT_ID: optional(z.string().min(1)),
    GOOGLE_CLIENT_SECRET: optional(z.string().min(1)),

    /**
     * Email from the app: "Email me a sign-in link", and registration by an emailed code (ADR-042).
     * Off until a verified mail domain is set up: the link button is shown disabled, registration
     * is by Google only, and the server offers none of those endpoints.
     */
    EMAIL_LINK_SIGN_IN: z.enum(["on", "off"]).default("off"),
    EMAIL_TRANSPORT: z.enum(["console", "resend"]).default("console"),
    EMAIL_FROM: z.string().min(3).default("InsightfulCell <onboarding@resend.dev>"),
    RESEND_API_KEY: optional(z.string().min(1)),
    /**
     * Who may get a sign-in link or a registration code, comma separated (blank: anyone). While
     * email goes out from Resend's test sender, only the Resend account owner can receive it, so
     * list that address.
     */
    EMAIL_LINK_ALLOWED_EMAILS: z.string().default(""),

    /**
     * Sender of password-reset emails ("Forgot password?", on whenever EMAIL_LINK_SIGN_IN is,
     * ADR-043). Defaults to `no-reply@` on EMAIL_FROM's domain. PASSWORD_RESET is no longer read.
     */
    EMAIL_RESET_FROM: optional(z.string().min(3)),

    /**
     * Unlocks /adminboard (people, usage, model spend). At least 32 random characters; blank
     * switches the dashboard off (it is then a 404). Read on the server only, never sent to a
     * browser: the browser gets a short-lived signed cookie instead.
     */
    ADMIN_DASHBOARD_KEY: optional(z.string().min(32, "Use at least 32 random characters")),
    /**
     * How long an unlocked dashboard page can switch period without the key again. A reload or a
     * new visit always asks for the key (ADR-044).
     */
    ADMIN_SESSION_MINUTES: z.coerce.number().int().min(5).max(720).default(30),
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
    for (const entry of listOrigins(env.EMAIL_LINK_ALLOWED_EMAILS)) {
      if (!EMAIL_PATTERN.test(entry)) {
        ctx.addIssue({
          code: "custom",
          path: ["EMAIL_LINK_ALLOWED_EMAILS"],
          message: `"${entry}" is not an email address`,
        });
      }
    }
    for (const entry of listOrigins(env.AUTH_TRUSTED_ORIGINS)) {
      const origin = originOf(entry);
      if (origin === null) {
        ctx.addIssue({
          code: "custom",
          path: ["AUTH_TRUSTED_ORIGINS"],
          message: `"${entry}" is not an origin: use scheme://host[:port], with no path`,
        });
      } else if (env.BETTER_AUTH_URL.startsWith("https://") && !origin.startsWith("https://")) {
        ctx.addIssue({
          code: "custom",
          path: ["AUTH_TRUSTED_ORIGINS"],
          message: `"${entry}" must be https while BETTER_AUTH_URL is (session cookies are Secure)`,
        });
      }
    }
    if (env.APP_ENV === "production") {
      const browserFacing = [
        ["BETTER_AUTH_URL", env.BETTER_AUTH_URL],
        ["API_PUBLIC_URL", env.API_PUBLIC_URL ?? env.API_BASE_URL],
        ["S3_PUBLIC_ORIGIN", env.S3_PUBLIC_ORIGIN],
      ] as const;
      for (const [key, value] of browserFacing) {
        if (isLoopback(value)) {
          ctx.addIssue({
            code: "custom",
            path: [key],
            message: "Points at localhost: browsers on other machines cannot reach it",
          });
        }
      }
      if (!env.S3_PUBLIC_ORIGIN.startsWith("https://")) {
        ctx.addIssue({
          code: "custom",
          path: ["S3_PUBLIC_ORIGIN"],
          message: "Must be https in production (browsers upload to it from an https page)",
        });
      }
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
      // Email in production goes to everyone (links, codes, resets), so it needs a verified
      // domain: Resend's shared test sender only reaches the Resend account owner.
      if (env.EMAIL_LINK_SIGN_IN === "on" && env.EMAIL_TRANSPORT === "console") {
        ctx.addIssue({
          code: "custom",
          path: ["EMAIL_TRANSPORT"],
          message:
            "The console transport prints sign-in links, codes and reset links and is not allowed in production",
        });
      }
      if (env.EMAIL_LINK_SIGN_IN === "on" && RESEND_TEST_SENDER.test(env.EMAIL_FROM)) {
        ctx.addIssue({
          code: "custom",
          path: ["EMAIL_FROM"],
          message: "Resend's test sender only reaches the account owner: use a verified domain",
        });
      } else if (env.EMAIL_LINK_SIGN_IN === "on" && RESEND_TEST_SENDER.test(resetSender(env))) {
        ctx.addIssue({
          code: "custom",
          path: ["EMAIL_RESET_FROM"],
          message: "Resend's test sender only reaches the account owner: use a verified domain",
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

function listOrigins(value: string): string[] {
  return value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/** `scheme://host[:port]` of an http(s) URL with nothing after it, or null. */
function originOf(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  const bare = url.pathname === "/" && !url.search && !url.hash && !url.username && !url.password;
  return (url.protocol === "http:" || url.protocol === "https:") && bare ? url.origin : null;
}

function isLoopback(value: string): boolean {
  return isLoopbackHost(new URL(value).hostname);
}

/**
 * Every origin the web app is served at: BETTER_AUTH_URL first, then AUTH_TRUSTED_ORIGINS.
 * Mutations (sign-in, the backend proxy) are accepted only from these.
 */
export function appOrigins(env: Env = getEnv()): string[] {
  const extra = listOrigins(env.AUTH_TRUSTED_ORIGINS).map(originOf);
  return [...new Set([new URL(env.BETTER_AUTH_URL).origin, ...extra.filter((o) => o !== null)])];
}

/** Addresses allowed to get a sign-in link, lower case; empty means anyone. */
export function emailLinkAllowList(env: Env = getEnv()): string[] {
  return listOrigins(env.EMAIL_LINK_ALLOWED_EMAILS).map((entry) => entry.toLowerCase());
}

/** Whether `email` may get a sign-in link. */
export function emailLinkAllowed(email: string, env: Env = getEnv()): boolean {
  const allowed = emailLinkAllowList(env);
  return allowed.length === 0 || allowed.includes(email.trim().toLowerCase());
}

/** "Forgot password?" is offered whenever the app sends email (ADR-043). */
export function passwordResetOn(env: Pick<Env, "EMAIL_LINK_SIGN_IN"> = getEnv()): boolean {
  return env.EMAIL_LINK_SIGN_IN === "on";
}

/**
 * Who password-reset emails come from: EMAIL_RESET_FROM, else `no-reply@` on EMAIL_FROM's domain
 * (`InsightfulCell <login@ic.example>` → `InsightfulCell <no-reply@ic.example>`). Resend's shared
 * test sender has one address only, so it stays as it is.
 */
export function resetSender(env: Pick<Env, "EMAIL_RESET_FROM" | "EMAIL_FROM"> = getEnv()): string {
  if (env.EMAIL_RESET_FROM) return env.EMAIL_RESET_FROM;
  if (RESEND_TEST_SENDER.test(env.EMAIL_FROM)) return env.EMAIL_FROM;
  return env.EMAIL_FROM.replace(/[^\s<@]+@(?=[^@\s>]+>?\s*$)/, "no-reply@");
}

/** Where browsers reach the API (live progress only; everything else goes through the BFF). */
export function apiPublicUrl(env: Env = getEnv()): string {
  return (env.API_PUBLIC_URL ?? env.API_BASE_URL).replace(/\/+$/, "");
}
