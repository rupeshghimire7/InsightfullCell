import "server-only";
import type { BetterAuthOptions } from "better-auth";
import { APIError } from "better-auth/api";
import { nextCookies } from "better-auth/next-js";
import { jwt, magicLink, organization } from "better-auth/plugins";
import type { Pool } from "pg";
import type { LinkDelivery } from "@/components/auth/sign-in-form";
import {
  MAGIC_LINK_TTL_SECONDS,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
} from "@/lib/auth-constants";
import { EmailDeliveryError, isResendApiKey, sendSignInEmail } from "@/lib/email";
import { getEnv } from "@/lib/env";
import { ac, roles } from "@/lib/permissions";
import { createIdentity, type OrganizationApi } from "@/lib/identity";
import { linkDelivery } from "@/lib/sign-in-options";

const DAY = 60 * 60 * 24;

/**
 * Better Auth options. A function of the connection pool (and a late-bound API handle) so the
 * migration script and the running app build the *same* schema-defining configuration.
 */
export function buildAuthOptions(pool: Pool, getApi: () => OrganizationApi = unboundApi) {
  const env = getEnv();

  const identity = createIdentity(pool, getApi);

  return {
    appName: "InsightfulCell",
    baseURL: env.BETTER_AUTH_URL,
    secret: env.BETTER_AUTH_SECRET,
    database: pool,
    // Registration with a password needs no mail provider: no verification email or code is sent
    // (ADR-028). Google, and sign-in links when switched on, are there for people who prefer not
    // to keep a password.
    emailAndPassword: {
      enabled: true,
      autoSignIn: true,
      minPasswordLength: PASSWORD_MIN_LENGTH,
      maxPasswordLength: PASSWORD_MAX_LENGTH,
    },
    socialProviders:
      env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET
        ? { google: { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET } }
        : {},
    session: {
      expiresIn: 7 * DAY,
      updateAge: DAY,
      // Off: a cached session cookie is trusted without asking the database, so a signed-out
      // (or revoked) session kept working, and minting API tokens, until the cache expired.
      // Every session check reads the database instead; signing out ends access at once.
      cookieCache: { enabled: false },
    },
    // The API token is minted on the server only (BFF, `getApiToken`). Better Auth's JWT plugin
    // would also hand it to any page script at GET /api/auth/token; that route does not exist.
    disabledPaths: ["/token"],
    rateLimit: { enabled: true, storage: "database", window: 60, max: 60 },
    advanced: {
      // The API identifies users and organisations by UUID.
      database: { generateId: "uuid" },
      useSecureCookies: env.BETTER_AUTH_URL.startsWith("https://"),
    },
    databaseHooks: {
      session: {
        create: {
          before: async (session) => ({
            data: {
              ...session,
              activeOrganizationId: await identity.ensureOrganization(session.userId),
            },
          }),
        },
      },
    },
    plugins: [
      // Switched off (EMAIL_LINK_SIGN_IN=off), the endpoint does not exist: a hidden button is not
      // a disabled feature. It uses only Better Auth's core tables, so the schema is the same.
      ...(env.EMAIL_LINK_SIGN_IN === "on"
        ? [
            magicLink({
              expiresIn: MAGIC_LINK_TTL_SECONDS,
              storeToken: "hashed",
              rateLimit: { window: 60, max: env.AUTH_MAGIC_LINK_RATE_MAX },
              sendMagicLink: async ({ email, url, metadata }) => {
                const apiKey = userResendKey(metadata, linkDelivery(env));
                try {
                  await sendSignInEmail({ to: email, url }, { apiKey });
                } catch (error) {
                  if (error instanceof EmailDeliveryError) {
                    throw emailProblem(error, Boolean(apiKey));
                  }
                  throw error;
                }
              },
            }),
          ]
        : []),
      organization({
        ac,
        roles,
        creatorRole: "owner",
        // One workspace per person in this release; teams and invitations arrive in IC-1xx.
        organizationLimit: 1,
      }),
      jwt({
        jwks: { keyPairConfig: { alg: "EdDSA", crv: "Ed25519" }, rotationInterval: 30 * DAY },
        // Nor in a `set-auth-jwt` header on session responses: the browser never holds it.
        disableSettingJwtHeader: true,
        jwt: {
          issuer: env.BETTER_AUTH_URL,
          audience: env.AUTH_JWT_AUDIENCE,
          expirationTime: "15m",
          // The API trusts exactly these claims and nothing from request bodies.
          definePayload: async ({ session }) =>
            identity.tokenClaims(
              session.userId,
              (session as { activeOrganizationId?: string | null }).activeOrganizationId,
            ),
        },
      }),
      nextCookies(), // must be last: lets server actions set the session cookie
    ],
  } satisfies BetterAuthOptions;
}

function unboundApi(): OrganizationApi {
  throw new Error("The auth API is not available while building options for a migration.");
}

/**
 * The Resend API key a tester typed on the sign-in page, if any (`metadata.resendApiKey`).
 * Accepted only while email is in testing mode (links printed to the log, or sent from Resend's
 * test sender): once a verified domain is configured, the server's own key is the only one used.
 */
export function userResendKey(metadata: unknown, delivery: LinkDelivery): string | undefined {
  const supplied =
    typeof metadata === "object" && metadata !== null
      ? (metadata as { resendApiKey?: unknown }).resendApiKey
      : undefined;
  if (supplied === undefined || supplied === "") return undefined;
  if (delivery === "email" || delivery === "off") {
    throw new APIError("BAD_REQUEST", {
      code: "RESEND_KEY_NOT_ACCEPTED",
      message: "This server sends sign-in email itself; leave the Resend API key out.",
    });
  }
  if (!isResendApiKey(supplied)) {
    throw new APIError("BAD_REQUEST", {
      code: "RESEND_KEY_INVALID",
      message: "That doesn't look like a Resend API key (they start with re_).",
    });
  }
  return supplied;
}

/** A failed delivery as an error the sign-in form can explain. Never echoes the key or link. */
function emailProblem(error: EmailDeliveryError, userKey: boolean): APIError {
  switch (error.reason) {
    case "invalid-key":
      return new APIError("BAD_REQUEST", {
        code: userKey ? "RESEND_KEY_INVALID" : "EMAIL_NOT_CONFIGURED",
        message: userKey
          ? "Resend didn't accept that API key."
          : "Sign-in email isn't set up correctly on this server.",
      });
    case "recipient-not-allowed":
      return new APIError("BAD_REQUEST", {
        code: "RESEND_RECIPIENT_NOT_ALLOWED",
        message: "Resend's test sender only delivers to the Resend account owner's address.",
      });
    case "rate-limited":
      return new APIError("TOO_MANY_REQUESTS", {
        code: "EMAIL_RATE_LIMITED",
        message: "The email provider is rate-limiting requests.",
      });
    default:
      return new APIError("BAD_GATEWAY", {
        code: "EMAIL_DELIVERY_FAILED",
        message: "The email provider didn't send the message.",
      });
  }
}
