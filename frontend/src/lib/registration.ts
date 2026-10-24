import "server-only";
import type { AuthContext, BetterAuthPlugin } from "better-auth";
import { APIError, createAuthEndpoint } from "better-auth/api";
import * as z from "zod";
import {
  EMAIL_ALREADY_REGISTERED_MESSAGE,
  EMAIL_NOT_ALLOWED_MESSAGE,
  OTP_RESEND_COOLDOWN_SECONDS,
} from "@/lib/auth-constants";

/** The server-only Better Auth endpoint that stores a new code (emailOTP plugin) and returns it. */
export interface CodeApi {
  createVerificationOTP(input: {
    body: { email: string; type: "email-verification" };
  }): Promise<string>;
}

export interface RegistrationOptions {
  /** Late-bound: the auth instance exists only after its options are built. */
  getApi: () => CodeApi;
  /** EMAIL_LINK_ALLOWED_EMAILS: who may get any email from the app. */
  allowed: (email: string) => boolean;
  /** Delivers the code; throws an `APIError` the form can explain when delivery fails. */
  sendCode: (to: string, otp: string) => Promise<void>;
}

type Adapter = AuthContext["internalAdapter"];

const emailSchema = z.string().trim().toLowerCase().pipe(z.email());

/**
 * Registration by email, with no password at this step (ADR-042):
 * - `POST /register/email` `{ name, email }`: refuses an address that already has an account,
 *   otherwise creates the account, *unverified*, and emails a 6-digit code. Verifying the code
 *   (`POST /email-otp/verify-email`, emailOTP plugin) marks the address verified and signs in.
 * - `POST /email-code/send` `{ email }`: "Send a new code", for a registration not finished yet
 *   or an older account whose address was never verified. Anyone else gets the same success
 *   answer and no email.
 *
 * An account that was created here but never verified (no password, no Google, no proof) is a
 * *pending* registration: registering the address again just sends a new code, so a stranger who
 * typed someone else's address cannot lock them out.
 */
export function registration({ getApi, allowed, sendCode }: RegistrationOptions) {
  /** At most one code per address per cooldown; released again if the email is not sent. */
  async function reserveCooldown(adapter: Adapter, email: string): Promise<() => Promise<void>> {
    const identifier = `email-code-cooldown:${email}`;
    const cooldown = (until: Date | undefined) => {
      const wait = until
        ? Math.max(1, Math.ceil((until.getTime() - Date.now()) / 1000))
        : OTP_RESEND_COOLDOWN_SECONDS;
      return new APIError("TOO_MANY_REQUESTS", {
        code: "CODE_COOLDOWN",
        message: `We just sent a code. Wait ${wait} seconds before asking for another.`,
      });
    };
    const last = await adapter.findVerificationValue(identifier);
    if (last && last.expiresAt > new Date()) throw cooldown(last.expiresAt);
    if (last) await adapter.deleteVerificationByIdentifier(identifier);
    // First writer wins (a fixed primary key), so two requests at once still send one code.
    const reserved = await adapter.reserveVerificationValue({
      identifier,
      value: "sent",
      expiresAt: new Date(Date.now() + OTP_RESEND_COOLDOWN_SECONDS * 1000),
    });
    if (!reserved) throw cooldown((await adapter.findVerificationValue(identifier))?.expiresAt);
    return () => adapter.deleteVerificationByIdentifier(identifier);
  }

  async function emailCode(adapter: Adapter, email: string): Promise<void> {
    const release = await reserveCooldown(adapter, email);
    try {
      const otp = await getApi().createVerificationOTP({
        body: { email, type: "email-verification" },
      });
      await sendCode(email, otp);
    } catch (error) {
      await release();
      throw error;
    }
  }

  function refuseIfNotAllowed(email: string) {
    if (!allowed(email)) {
      throw new APIError("FORBIDDEN", {
        code: "EMAIL_NOT_ALLOWED",
        message: EMAIL_NOT_ALLOWED_MESSAGE,
      });
    }
  }

  return {
    id: "registration",
    endpoints: {
      registerEmail: createAuthEndpoint(
        "/register/email",
        {
          method: "POST",
          body: z.object({ name: z.string().trim().min(1).max(100), email: emailSchema }),
        },
        async (ctx) => {
          const { name, email } = ctx.body;
          refuseIfNotAllowed(email);
          const adapter = ctx.context.internalAdapter;
          const existing = await adapter.findUserByEmail(email, { includeAccounts: true });
          if (existing && (existing.user.emailVerified || existing.accounts.length > 0)) {
            throw new APIError("CONFLICT", {
              code: "EMAIL_ALREADY_REGISTERED",
              message: EMAIL_ALREADY_REGISTERED_MESSAGE,
            });
          }
          if (existing) await adapter.updateUser(existing.user.id, { name });
          else
            await adapter.createUser(
              { email, name, emailVerified: false },
              { method: "email-otp" },
            );
          await emailCode(adapter, email);
          return ctx.json({ status: true });
        },
      ),
      sendEmailCode: createAuthEndpoint(
        "/email-code/send",
        { method: "POST", body: z.object({ email: emailSchema }) },
        async (ctx) => {
          const { email } = ctx.body;
          refuseIfNotAllowed(email);
          const adapter = ctx.context.internalAdapter;
          const existing = await adapter.findUserByEmail(email);
          if (existing && !existing.user.emailVerified) {
            await emailCode(adapter, email);
          }
          return ctx.json({ status: true });
        },
      ),
    },
  } satisfies BetterAuthPlugin;
}
