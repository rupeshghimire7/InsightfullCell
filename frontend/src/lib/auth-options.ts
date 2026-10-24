import "server-only";
import type { BetterAuthOptions } from "better-auth";
import { APIError } from "better-auth/api";
import { nextCookies } from "better-auth/next-js";
import { jwt, magicLink, organization } from "better-auth/plugins";
import type { Pool } from "pg";
import {
  MAGIC_LINK_TTL_SECONDS,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_RESET_TTL_SECONDS,
} from "@/lib/auth-constants";
import { EMAIL_NOT_ALLOWED_MESSAGE } from "@/lib/auth-constants";
import { EmailDeliveryError, sendPasswordResetEmail, sendSignInEmail } from "@/lib/email";
import { appOrigins, emailLinkAllowed, getEnv } from "@/lib/env";
import { ac, roles } from "@/lib/permissions";
import { createIdentity, type OrganizationApi } from "@/lib/identity";

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
    // Sign-in POSTs are refused (403 INVALID_ORIGIN) unless the page's origin is listed: by
    // default only BETTER_AUTH_URL's, so a tunnel or second domain goes in AUTH_TRUSTED_ORIGINS.
    trustedOrigins: appOrigins(env),
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
      // "Forgot password?" (ADR-041). Switched off (PASSWORD_RESET=off), no reset email is sent.
      ...(env.PASSWORD_RESET === "on"
        ? {
            resetPasswordTokenExpiresIn: PASSWORD_RESET_TTL_SECONDS,
            // A password someone else learned stops working everywhere at once.
            revokeSessionsOnPasswordReset: true,
            sendResetPassword: async ({ user, url }: { user: { email: string }; url: string }) => {
              // Not awaited, and failures only logged: the answer and its timing must be the same
              // whether or not the address has an account. The message never holds the link.
              void sendPasswordResetEmail({ to: user.email, url }).catch((error: unknown) => {
                console.error(
                  `[auth] password reset email not sent: ${error instanceof Error ? error.message : "unknown error"}`,
                );
              });
            },
          }
        : {}),
    },
    socialProviders:
      env.GOOGLE_SIGN_IN === "on" && env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET
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
    rateLimit: {
      enabled: true,
      storage: "database",
      window: 60,
      max: 60,
      // Each request may send an email: three a minute from one address (IP) is plenty.
      customRules: { "/request-password-reset": { window: 60, max: 3 } },
    },
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
              sendMagicLink: async ({ email, url }) => {
                // Checked before anything is sent; the answer never names who is allowed.
                if (!emailLinkAllowed(email, env)) {
                  throw new APIError("FORBIDDEN", {
                    code: "EMAIL_NOT_ALLOWED",
                    message: EMAIL_NOT_ALLOWED_MESSAGE,
                  });
                }
                try {
                  await sendSignInEmail({ to: email, url });
                } catch (error) {
                  if (error instanceof EmailDeliveryError) throw emailProblem(error);
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

/** A failed delivery as an error the sign-in form can explain. Never echoes the key or link. */
function emailProblem(error: EmailDeliveryError): APIError {
  switch (error.reason) {
    case "invalid-key":
      return new APIError("BAD_REQUEST", {
        code: "EMAIL_NOT_CONFIGURED",
        message: "Sign-in email isn't set up correctly on this server.",
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
