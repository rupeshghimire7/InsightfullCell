import type { Pool } from "pg";
import { afterEach, describe, expect, it, vi } from "vitest";
import type * as EmailModule from "@/lib/email";

const sendSignInEmail = vi.fn();
const sendPasswordResetEmail = vi.fn();
const sendOtpEmail = vi.fn();
vi.mock("@/lib/email", async (original) => ({
  ...(await original<typeof EmailModule>()),
  sendSignInEmail: (...args: unknown[]) => sendSignInEmail(...args),
  sendPasswordResetEmail: (...args: unknown[]) => sendPasswordResetEmail(...args),
  sendOtpEmail: (...args: unknown[]) => sendOtpEmail(...args),
}));

describe("buildAuthOptions", () => {
  const base = {
    BETTER_AUTH_URL: "http://localhost:3000",
    BETTER_AUTH_SECRET: "x".repeat(32),
    AUTH_DATABASE_URL: "postgres://u:p@localhost/db",
    API_BASE_URL: "http://localhost:8000",
    S3_PUBLIC_ORIGIN: "http://localhost:9000",
  };

  async function options(env: Record<string, string> = {}) {
    vi.resetModules();
    for (const [key, value] of Object.entries({ ...base, ...env })) vi.stubEnv(key, value);
    const { buildAuthOptions } = await import("@/lib/auth-options");
    return buildAuthOptions({} as Pool);
  }

  async function pluginIds(env: Record<string, string>): Promise<string[]> {
    return (await options(env)).plugins.map((plugin) => plugin.id);
  }

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("offers Google only when switched on and configured", async () => {
    const google = { GOOGLE_CLIENT_ID: "id", GOOGLE_CLIENT_SECRET: "s" };
    expect((await options(google)).socialProviders).toEqual({});
    expect((await options({ ...google, GOOGLE_SIGN_IN: "on" })).socialProviders).toHaveProperty(
      "google",
    );
  });

  it("offers no sign-in link, code or email registration while email is off (the default)", async () => {
    const ids = await pluginIds({});
    for (const id of ["magic-link", "email-otp", "registration"]) expect(ids).not.toContain(id);
    const built = await options();
    // no way to verify an address, so older unverified password accounts keep signing in
    expect(built.emailAndPassword.requireEmailVerification).toBe(false);
    expect(built).not.toHaveProperty("emailVerification");
  });

  it("offers them once switched on", async () => {
    const ids = await pluginIds({ EMAIL_LINK_SIGN_IN: "on" });
    for (const id of ["magic-link", "email-otp", "registration"]) expect(ids).toContain(id);
  });

  describe("accounts are created by registering only (ADR-042)", () => {
    const on = { EMAIL_LINK_SIGN_IN: "on", AUTH_MAGIC_LINK_RATE_MAX: "7" };
    type PluginWithOptions = { id: string; options?: Record<string, unknown> };
    const plugin = async (id: string) =>
      ((await options(on)).plugins as PluginWithOptions[]).find((p) => p.id === id)!;

    it("has no password sign-up, and links never create an account", async () => {
      expect((await options()).disabledPaths).toEqual(
        expect.arrayContaining(["/sign-up/email", "/token"]),
      );
      expect((await plugin("magic-link")).options).toMatchObject({ disableSignUp: true });
    });

    it("lets Google create an account only from 'Sign up with Google'", async () => {
      const built = await options({
        GOOGLE_SIGN_IN: "on",
        GOOGLE_CLIENT_ID: "id",
        GOOGLE_CLIENT_SECRET: "s",
      });
      expect(built.socialProviders.google).toMatchObject({ disableImplicitSignUp: true });
      // implicit linking stays safe: only verified accounts, no trusted-provider shortcut
      expect(built).not.toHaveProperty("account");
    });

    it("uses 6-digit codes that last 10 minutes, stored hashed, three guesses each", async () => {
      expect((await plugin("email-otp")).options).toMatchObject({
        otpLength: 6,
        expiresIn: 600,
        allowedAttempts: 3,
        storeOTP: "hashed",
        disableSignUp: true,
        overrideDefaultEmailVerification: true,
      });
    });

    it("verifies an address before its password signs in, and signs in once verified", async () => {
      const built = await options(on);
      expect(built.emailAndPassword.requireEmailVerification).toBe(true);
      expect(built.emailVerification).toEqual({
        sendOnSignIn: true,
        autoSignInAfterVerification: true,
      });
    });

    it("rate-limits sending and checking codes per IP", async () => {
      const rules = (await options(on)).rateLimit.customRules;
      for (const path of ["/register/email", "/email-code/send", "/email-otp/verify-email"]) {
        expect(rules).toHaveProperty([path], { window: 60, max: 7 });
      }
    });

    it("has none of the emailOTP routes this app does not use", async () => {
      expect((await options(on)).disabledPaths).toEqual(
        expect.arrayContaining([
          "/sign-in/email-otp",
          "/email-otp/send-verification-otp",
          "/email-otp/reset-password",
          "/forget-password/email-otp",
          "/send-verification-email",
        ]),
      );
    });

    it("emails only verification codes, to allowed addresses, and only logs a failure", async () => {
      const send = async (email: string, type: string, allowed = "") => {
        const built = await options({ ...on, EMAIL_LINK_ALLOWED_EMAILS: allowed });
        const otp = (built.plugins as PluginWithOptions[]).find((p) => p.id === "email-otp")!
          .options as {
          sendVerificationOTP: (data: {
            email: string;
            otp: string;
            type: string;
          }) => Promise<void>;
        };
        return otp.sendVerificationOTP({ email, otp: "123456", type });
      };
      sendOtpEmail.mockReset().mockResolvedValue(undefined);
      await send("ada@example.com", "sign-in");
      await send("ada@example.com", "email-verification", "owner@example.com");
      expect(sendOtpEmail).not.toHaveBeenCalled();
      await send("ada@example.com", "email-verification");
      expect(sendOtpEmail).toHaveBeenCalledWith({ to: "ada@example.com", otp: "123456" });

      const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
      sendOtpEmail.mockRejectedValue(new Error("HTTP 500"));
      await expect(send("ada@example.com", "email-verification")).resolves.toBeUndefined();
      expect(String(logged.mock.calls[0]?.[0])).toContain("verification code not sent");
      expect(String(logged.mock.calls[0]?.[0])).not.toMatch(/123456|ada@example\.com/);
    });

    it("deletes accounts only through the server ('Delete my account'), never by address", async () => {
      const built = await options(on);
      expect(built.user.deleteUser.enabled).toBe(true);
      expect(built.disabledPaths).toEqual(
        expect.arrayContaining(["/delete-user", "/delete-user/callback", "/change-email"]),
      );
    });

    it("offers password reset whenever email is on", async () => {
      // (off first: the stubbed environment lasts until the test ends)
      expect((await options()).emailAndPassword).not.toHaveProperty("sendResetPassword");
      expect((await options(on)).emailAndPassword).toHaveProperty("sendResetPassword");
    });

    it("guards every request with the password policy and the sign-in link rules", async () => {
      expect((await options()).hooks.before).toBeTypeOf("function");
    });
  });

  it("trusts sign-in requests from every origin the app is served at", async () => {
    expect((await options()).trustedOrigins).toEqual(["http://localhost:3000"]);
    const built = await options({ AUTH_TRUSTED_ORIGINS: "https://abcd.ngrok-free.app" });
    expect(built.trustedOrigins).toEqual(["http://localhost:3000", "https://abcd.ngrok-free.app"]);
  });

  it("never hands the API token to the browser", async () => {
    const built = await options();
    expect(built.disabledPaths).toContain("/token");
    const jwt = built.plugins.find((plugin) => plugin.id === "jwt");
    expect(jwt?.options).toMatchObject({ disableSettingJwtHeader: true });
  });

  describe("sign-in links for allowed addresses only", () => {
    async function send(email: string, allowed: string) {
      const built = await options({
        EMAIL_LINK_SIGN_IN: "on",
        EMAIL_LINK_ALLOWED_EMAILS: allowed,
      });
      const plugin = built.plugins.find((p) => p.id === "magic-link") as unknown as {
        options: { sendMagicLink: (data: { email: string; url: string }) => Promise<void> };
      };
      return plugin.options.sendMagicLink({ email, url: "https://app.test/verify?token=t" });
    }

    it("sends to a listed address, whatever its case", async () => {
      sendSignInEmail.mockResolvedValue(undefined);
      await send("Owner@Example.com", "owner@example.com, other@example.com");
      expect(sendSignInEmail).toHaveBeenCalledWith({
        to: "Owner@Example.com",
        url: "https://app.test/verify?token=t",
      });
    });

    it("refuses anyone else before sending, without naming who is allowed", async () => {
      sendSignInEmail.mockReset();
      let caught: unknown;
      try {
        await send("stranger@example.com", "owner@example.com");
      } catch (error) {
        caught = error;
      }
      expect(caught).toMatchObject({ body: { code: "EMAIL_NOT_ALLOWED" } });
      expect(JSON.stringify(caught)).not.toContain("owner@example.com");
      expect(sendSignInEmail).not.toHaveBeenCalled();
    });

    it("sends to anyone while no list is set", async () => {
      sendSignInEmail.mockReset().mockResolvedValue(undefined);
      await send("anyone@example.com", "");
      expect(sendSignInEmail).toHaveBeenCalledTimes(1);
    });
  });

  describe("password reset", () => {
    type ResetOptions = {
      sendResetPassword?: (data: { user: { email: string }; url: string }) => Promise<void>;
      resetPasswordTokenExpiresIn?: number;
      revokeSessionsOnPasswordReset?: boolean;
    };
    const reset = async (env: Record<string, string> = {}) =>
      (await options(env)).emailAndPassword as ResetOptions;

    it("sends no reset emails while it is off (the default)", async () => {
      const built = await reset();
      expect(built.sendResetPassword).toBeUndefined();
      expect(built.revokeSessionsOnPasswordReset).toBeUndefined();
    });

    it("once on, links last 30 minutes and a reset signs every session out", async () => {
      const built = await reset({ EMAIL_LINK_SIGN_IN: "on" });
      expect(built.resetPasswordTokenExpiresIn).toBe(30 * 60);
      expect(built.revokeSessionsOnPasswordReset).toBe(true);
      expect(built.sendResetPassword).toBeTypeOf("function");
    });

    it("does not wait for the email, so the answer is the same for every address", async () => {
      let deliver: () => void = () => undefined;
      sendPasswordResetEmail.mockReset().mockReturnValue(
        new Promise<void>((resolve) => {
          deliver = resolve;
        }),
      );
      const built = await reset({ EMAIL_LINK_SIGN_IN: "on" });
      // Resolves although the delivery above never finished.
      await built.sendResetPassword?.({ user: { email: "ada@example.com" }, url: "https://x/r" });
      expect(sendPasswordResetEmail).toHaveBeenCalledWith({
        to: "ada@example.com",
        url: "https://x/r",
      });
      deliver();
    });

    it("only logs a failed delivery, never the link or the address", async () => {
      const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
      sendPasswordResetEmail.mockReset().mockRejectedValue(new Error("HTTP 500"));
      const built = await reset({ EMAIL_LINK_SIGN_IN: "on" });
      await expect(
        built.sendResetPassword?.({ user: { email: "ada@example.com" }, url: "https://x/secret" }),
      ).resolves.toBeUndefined();
      await vi.waitFor(() => expect(logged).toHaveBeenCalled());
      const line = String(logged.mock.calls[0]?.[0]);
      expect(line).toContain("password reset email not sent");
      expect(line).not.toMatch(/secret|ada@example\.com/);
      logged.mockRestore();
    });

    it("limits reset requests to three a minute", async () => {
      expect((await options()).rateLimit.customRules).toEqual({
        "/request-password-reset": { window: 60, max: 3 },
      });
    });
  });

  it("checks the database on every request, so signing out ends access at once", async () => {
    expect((await options()).session.cookieCache).toEqual({ enabled: false });
  });
});
