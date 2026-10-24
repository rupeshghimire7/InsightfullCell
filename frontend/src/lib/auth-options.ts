import "server-only";
import type { AuthContext, BetterAuthOptions } from "better-auth";
import { APIError, createAuthMiddleware, getSessionFromCtx } from "better-auth/api";
import { nextCookies } from "better-auth/next-js";
import { emailOTP, jwt, magicLink, organization } from "better-auth/plugins";
import type { Pool } from "pg";
import {
  ACCOUNT_NOT_FOUND_MESSAGE,
  EMAIL_NOT_ALLOWED_MESSAGE,
  MAGIC_LINK_TTL_SECONDS,
  OTP_ALLOWED_ATTEMPTS,
  OTP_LENGTH,
  OTP_TTL_SECONDS,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_RESET_TTL_SECONDS,
} from "@/lib/auth-constants";
import {
  EmailDeliveryError,
  sendOtpEmail,
  sendPasswordResetEmail,
  sendSignInEmail,
} from "@/lib/email";
import { appOrigins, emailLinkAllowed, getEnv, passwordResetOn, type Env } from "@/lib/env";
import { ac, roles } from "@/lib/permissions";
import { createIdentity, type OrganizationApi } from "@/lib/identity";
import { passwordPolicyError } from "@/lib/password-policy";
import { registration, type CodeApi } from "@/lib/registration";

const DAY = 60 * 60 * 24;

/** The slice of the auth API the options call back into, bound after the instance exists. */
export type AuthApi = OrganizationApi & CodeApi;

/**
 * Endpoints nobody may call: the API token (minted on the server only), and every way in that is
 * not one of ours (ADR-042). Registration is `/register/email` (or Google from the register
 * page), so password sign-up is gone; the emailOTP plugin is used only to verify an address, so
 * its OTP sign-in, password reset and change-email routes are gone too, as are the core
 * verification-link routes it replaces.
 */
export const DISABLED_PATHS = [
  "/token",
  // Deleting an account also erases its data in the API, so only the server action may do it
  // ("Delete my account", ADR-043); the address an account signs in with never changes.
  "/delete-user",
  "/delete-user/callback",
  "/change-email",
  "/sign-up/email",
  "/send-verification-email",
  "/verify-email",
  "/sign-in/email-otp",
  "/email-otp/send-verification-otp",
  "/email-otp/check-verification-otp",
  "/email-otp/request-password-reset",
  "/forget-password/email-otp",
  "/email-otp/reset-password",
  "/email-otp/request-email-change",
  "/email-otp/change-email",
];

/**
 * Better Auth options. A function of the connection pool (and a late-bound API handle) so the
 * migration script and the running app build the *same* schema-defining configuration.
 */
export function buildAuthOptions(pool: Pool, getApi: () => AuthApi = unboundApi) {
  const env = getEnv();

  const identity = createIdentity(pool, getApi);
  // Registration and address verification need real email: with it off, only Google registers.
  const emailOn = env.EMAIL_LINK_SIGN_IN === "on";

  return {
    appName: "InsightfulCell",
    baseURL: env.BETTER_AUTH_URL,
    // Sign-in POSTs are refused (403 INVALID_ORIGIN) unless the page's origin is listed: by
    // default only BETTER_AUTH_URL's, so a tunnel or second domain goes in AUTH_TRUSTED_ORIGINS.
    trustedOrigins: appOrigins(env),
    secret: env.BETTER_AUTH_SECRET,
    database: pool,
    // Passwords sign in, but nobody signs *up* with one (ADR-042): an account is created by
    // registering (an emailed code, or Google), then the person sets a password (`/set-password`).
    emailAndPassword: {
      enabled: true,
      // An account whose address was never verified (registered with a password before ADR-042)
      // gets no session from its password: it is sent a code to verify first. Without email
      // delivery there is no way to verify, so those accounts keep signing in as before.
      requireEmailVerification: emailOn,
      minPasswordLength: PASSWORD_MIN_LENGTH,
      maxPasswordLength: PASSWORD_MAX_LENGTH,
      // "Forgot password?" (ADR-041), whenever email is on (ADR-043); from no-reply@.
      ...(passwordResetOn(env)
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
    // The code above signs in once verified (and the session hook creates the workspace). The
    // emailOTP plugin replaces the verification email with the code (`sendVerificationOTP`).
    ...(emailOn
      ? { emailVerification: { sendOnSignIn: true, autoSignInAfterVerification: true } }
      : {}),
    socialProviders:
      env.GOOGLE_SIGN_IN === "on" && env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET
        ? {
            google: {
              clientId: env.GOOGLE_CLIENT_ID,
              clientSecret: env.GOOGLE_CLIENT_SECRET,
              // Only "Sign up with Google" (`requestSignUp: true`) creates an account; on the
              // sign-in page an unknown Google address is refused (`signup_disabled`).
              disableImplicitSignUp: true,
            },
          }
        : {},
    user: {
      deleteUser: {
        enabled: true,
        beforeDelete: async (user: { id: string; email: string }) => {
          await identity.forgetUser(user.id, user.email);
        },
      },
    },
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
    disabledPaths: DISABLED_PATHS,
    rateLimit: {
      enabled: true,
      storage: "database",
      window: 60,
      max: 60,
      // Each request may send an email: three a minute from one address (IP) is plenty. Codes are
      // limited per IP here and, in `registration.ts`, to one a minute per email address.
      customRules: {
        "/request-password-reset": { window: 60, max: 3 },
        ...(emailOn
          ? {
              "/register/email": { window: 60, max: env.AUTH_MAGIC_LINK_RATE_MAX },
              "/email-code/send": { window: 60, max: env.AUTH_MAGIC_LINK_RATE_MAX },
              "/email-otp/verify-email": { window: 60, max: env.AUTH_MAGIC_LINK_RATE_MAX },
            }
          : {}),
      },
    },
    advanced: {
      // The API identifies users and organisations by UUID.
      database: { generateId: "uuid" },
      useSecureCookies: env.BETTER_AUTH_URL.startsWith("https://"),
    },
    hooks: { before: authGuards(env) },
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
              // A link never creates an account; `authGuards` also refuses an unknown address
              // before any link is stored or sent.
              disableSignUp: true,
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
            emailOTP({
              otpLength: OTP_LENGTH,
              expiresIn: OTP_TTL_SECONDS,
              allowedAttempts: OTP_ALLOWED_ATTEMPTS,
              storeOTP: "hashed",
              disableSignUp: true,
              // Better Auth's "verify your email" step sends this code instead of a link.
              overrideDefaultEmailVerification: true,
              rateLimit: { window: 60, max: env.AUTH_MAGIC_LINK_RATE_MAX },
              // Called by the password sign-in of an unverified account (`sendOnSignIn`). Better
              // Auth does not report a failure here, so it is only logged, without the code or
              // the address; "Send a new code" (`/email-code/send`) does report it.
              sendVerificationOTP: async ({ email, otp, type }) => {
                if (type !== "email-verification" || !emailLinkAllowed(email, env)) return;
                try {
                  await sendOtpEmail({ to: email, otp });
                } catch (error) {
                  console.error(
                    `[auth] verification code not sent: ${error instanceof Error ? error.message : "unknown error"}`,
                  );
                }
              },
            }),
            registration({
              getApi,
              allowed: (email) => emailLinkAllowed(email, env),
              sendCode: async (to, otp) => {
                try {
                  await sendOtpEmail({ to, otp });
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

function unboundApi(): AuthApi {
  throw new Error("The auth API is not available while building options for a migration.");
}

/** The person a reset token belongs to, without using the token up. */
async function resetTokenEmail(
  adapter: AuthContext["internalAdapter"],
  token: unknown,
): Promise<string | null> {
  if (typeof token !== "string" || !token) return null;
  const verification = await adapter.findVerificationValue(`reset-password:${token}`);
  if (!verification) return null;
  return (await adapter.findUserById(verification.value))?.email ?? null;
}

/**
 * Server-side rules no hand-made request can skip:
 * - a sign-in link goes only to an allowed address that already has an account;
 * - every new password meets the policy (`password-policy.ts`), wherever it is chosen. Better
 *   Auth's own length checks still run after this.
 */
export function authGuards(env: Env) {
  return createAuthMiddleware(async (ctx) => {
    const body = (ctx.body ?? {}) as Record<string, unknown>;
    const adapter = ctx.context.internalAdapter;
    if (ctx.path === "/sign-in/magic-link") {
      const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
      if (!email) return;
      if (!emailLinkAllowed(email, env)) {
        throw new APIError("FORBIDDEN", {
          code: "EMAIL_NOT_ALLOWED",
          message: EMAIL_NOT_ALLOWED_MESSAGE,
        });
      }
      if (!(await adapter.findUserByEmail(email))) {
        throw new APIError("BAD_REQUEST", {
          code: "ACCOUNT_NOT_FOUND",
          message: ACCOUNT_NOT_FOUND_MESSAGE,
        });
      }
      return;
    }
    // The profile changes the name only: never the address, nor anything else.
    if (ctx.path === "/update-user") {
      const name = typeof body.name === "string" ? body.name.trim() : "";
      if (Object.keys(body).some((key) => key !== "name") || !name || name.length > 100) {
        throw new APIError("BAD_REQUEST", {
          code: "INVALID_PROFILE",
          message: "Only your name can be changed: 1 to 100 characters.",
        });
      }
      return;
    }
    const operation = (ctx as { operationId?: string }).operationId;
    let password: unknown;
    let email: string | null = null;
    if (ctx.path === "/sign-up/email") {
      password = body.password;
      email = typeof body.email === "string" ? body.email : null;
    } else if (
      ctx.path === "/change-password" ||
      ctx.path === "/set-password" ||
      operation === "setPassword"
    ) {
      password = body.newPassword;
      email = (await getSessionFromCtx(ctx))?.user.email ?? null;
    } else if (ctx.path === "/reset-password") {
      password = body.newPassword;
      email = await resetTokenEmail(adapter, body.token ?? ctx.query?.token);
    } else {
      return;
    }
    if (typeof password !== "string") return; // Better Auth's own body validation answers
    const problem = passwordPolicyError(password, email);
    if (problem) throw new APIError("BAD_REQUEST", { code: "PASSWORD_TOO_WEAK", message: problem });
  });
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
        message: "The email provider refused to send to this address.",
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
