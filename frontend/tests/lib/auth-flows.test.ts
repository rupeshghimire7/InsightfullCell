// @vitest-environment node
import type { Pool } from "pg";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type * as EmailModule from "@/lib/email";

/**
 * The account rules of ADR-042 against a real Better Auth 1.7.7 instance (in-memory database),
 * through its HTTP handler, so hooks, plugins, rate limits and disabled paths all run as they do
 * in the app. Email is captured instead of sent; Google's token endpoint is stubbed.
 */
const sentCodes: { to: string; otp: string }[] = [];
const sentLinks: { to: string; url: string }[] = [];
vi.mock("@/lib/email", async (original) => ({
  ...(await original<typeof EmailModule>()),
  sendOtpEmail: async (message: { to: string; otp: string }) => {
    sentCodes.push(message);
  },
  sendSignInEmail: async (message: { to: string; url: string }) => {
    sentLinks.push(message);
  },
  sendPasswordResetEmail: async () => undefined,
}));
vi.mock("better-auth/next-js", () => ({ nextCookies: () => ({ id: "next-cookies" }) }));

const ORIGIN = "http://localhost:3000";
/** The SQL the identity module sends to the auth database (not the memory adapter). */
const poolQuery = vi.fn<(sql: string, params?: unknown[]) => Promise<{ rows: never[] }>>(
  async () => ({ rows: [] }),
);
const STRONG = "Str0ng!Passphrase";

type Db = Record<string, Record<string, unknown>[]>;
let db: Db;
let auth: Awaited<ReturnType<typeof build>>;
let ip = 0;

async function build() {
  const { betterAuth } = await import("better-auth");
  const { memoryAdapter } = await import("better-auth/adapters/memory");
  const { buildAuthOptions } = await import("@/lib/auth-options");
  const pool = { query: poolQuery } as unknown as Pool;
  const handle: { api?: { createVerificationOTP: (input: never) => Promise<string> } } = {};
  const options = buildAuthOptions(pool, () => ({
    createOrganization: async () => ({ id: "11111111-1111-4111-8111-111111111111" }),
    createVerificationOTP: (input) => handle.api!.createVerificationOTP(input as never),
  }));
  const google = options.socialProviders.google!;
  const instance = betterAuth({
    ...options,
    database: memoryAdapter(db),
    socialProviders: {
      google: {
        ...google,
        getUserInfo: (async () => ({
          user: {
            id: googleUser.id,
            email: googleUser.email,
            emailVerified: true,
            name: "Google Person",
          },
          data: { sub: googleUser.id },
        })) as never,
      },
    },
  });
  handle.api = instance.api as never;
  return instance;
}
const googleUser = { id: "google-1", email: "" };

/** A request from its own IP, so the per-IP limits never mix tests up. */
async function call(
  path: string,
  init: { body?: unknown; cookie?: string; method?: string } = {},
): Promise<Response> {
  ip += 1;
  return auth.handler(
    new Request(`${ORIGIN}/api/auth${path}`, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: {
        "content-type": "application/json",
        origin: ORIGIN,
        "x-forwarded-for": `10.0.${Math.floor(ip / 250)}.${ip % 250}`,
        ...(init.cookie ? { cookie: init.cookie } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    }),
  );
}

async function code(response: Response): Promise<string | undefined> {
  return ((await response.clone().json()) as { code?: string }).code;
}

function cookiesOf(response: Response): string {
  return response.headers
    .getSetCookie()
    .map((line) => line.split(";")[0])
    .join("; ");
}

const users = () => db.user as { id: string; email: string; emailVerified: boolean }[];
const accounts = () => db.account as { userId: string; providerId: string; password?: string }[];
const lastCode = (email: string) => sentCodes.filter((m) => m.to === email).at(-1)?.otp ?? "";

async function registerAndVerify(email: string): Promise<string> {
  expect((await call("/register/email", { body: { name: "Ada", email } })).status).toBe(200);
  const verified = await call("/email-otp/verify-email", { body: { email, otp: lastCode(email) } });
  expect(verified.status).toBe(200);
  return cookiesOf(verified);
}

beforeAll(async () => {
  const env = {
    BETTER_AUTH_URL: ORIGIN,
    BETTER_AUTH_SECRET: "x".repeat(32),
    AUTH_DATABASE_URL: "postgres://u:p@localhost/db",
    API_BASE_URL: "http://localhost:8000",
    S3_PUBLIC_ORIGIN: "http://localhost:9000",
    EMAIL_LINK_SIGN_IN: "on",
    EMAIL_TRANSPORT: "console",
    GOOGLE_SIGN_IN: "on",
    GOOGLE_CLIENT_ID: "client-id",
    GOOGLE_CLIENT_SECRET: "client-secret",
    AUTH_MAGIC_LINK_RATE_MAX: "1000",
  };
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  db = {
    user: [],
    session: [],
    account: [],
    verification: [],
    organization: [],
    member: [],
    invitation: [],
    jwks: [],
    rateLimit: [],
  };
  auth = await build();
  // Google's token endpoint: any code is accepted.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ access_token: "access", token_type: "Bearer", expires_in: 3600 }),
    ),
  );
});
afterAll(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  sentCodes.length = 0;
  sentLinks.length = 0;
});

describe("registration by email", () => {
  it("creates an unverified account, emails a code, and signs in once it is verified", async () => {
    const email = "ada@example.com";
    const registered = await call("/register/email", { body: { name: " Ada ", email } });
    expect(registered.status).toBe(200);
    expect(users().find((u) => u.email === email)).toMatchObject({ emailVerified: false });
    expect(lastCode(email)).toMatch(/^\d{6}$/);
    // stored hashed, never as typed
    expect(JSON.stringify(db.verification)).not.toContain(lastCode(email));

    const wrong = await call("/email-otp/verify-email", { body: { email, otp: "000000" } });
    expect(await code(wrong)).toBe("INVALID_OTP");

    const verified = await call("/email-otp/verify-email", {
      body: { email, otp: lastCode(email) },
    });
    expect(verified.status).toBe(200);
    expect(cookiesOf(verified)).toContain("session_token");
    expect(users().find((u) => u.email === email)).toMatchObject({ emailVerified: true });
    expect(
      accounts().filter((a) => a.userId === users().find((u) => u.email === email)!.id),
    ).toEqual([]);

    // a used code is refused
    const again = await call("/email-otp/verify-email", { body: { email, otp: lastCode(email) } });
    expect(await code(again)).toBe("INVALID_OTP");
  });

  it("refuses an address that is already registered", async () => {
    await registerAndVerify("taken@example.com");
    const response = await call("/register/email", {
      body: { name: "Mallory", email: "Taken@Example.com" },
    });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({
      code: "EMAIL_ALREADY_REGISTERED",
      message: "That email is already registered. Sign in instead.",
    });
  });

  it("sends one code a minute per address, and a pending registration can ask again later", async () => {
    const email = "pending@example.com";
    expect((await call("/register/email", { body: { name: "P", email } })).status).toBe(200);
    const soon = await call("/register/email", { body: { name: "P", email } });
    expect(soon.status).toBe(429);
    expect(await code(soon)).toBe("CODE_COOLDOWN");
    const resend = await call("/email-code/send", { body: { email } });
    expect(resend.status).toBe(429);
    // a minute later
    for (const row of db.verification as { identifier: string; expiresAt: Date }[]) {
      if (row.identifier.startsWith("email-code-cooldown:")) row.expiresAt = new Date(0);
    }
    expect((await call("/email-code/send", { body: { email } })).status).toBe(200);
    expect(sentCodes.filter((m) => m.to === email)).toHaveLength(2);
  });

  it("sends no code to an unknown or already verified address", async () => {
    await registerAndVerify("verified@example.com");
    sentCodes.length = 0;
    for (const email of ["nobody@example.com", "verified@example.com"]) {
      expect((await call("/email-code/send", { body: { email } })).status).toBe(200);
    }
    expect(sentCodes).toEqual([]);
  });

  it("refuses an expired code", async () => {
    const email = "late@example.com";
    await call("/register/email", { body: { name: "Late", email } });
    for (const row of db.verification as { identifier: string; expiresAt: Date }[]) {
      if (row.identifier === `email-verification-otp-${email}`) row.expiresAt = new Date(0);
    }
    const response = await call("/email-otp/verify-email", {
      body: { email, otp: lastCode(email) },
    });
    expect(await code(response)).toBe("OTP_EXPIRED");
  });

  it("spends the code after three wrong guesses", async () => {
    const email = "guesser@example.com";
    await call("/register/email", { body: { name: "G", email } });
    for (let i = 0; i < 3; i++) {
      await call("/email-otp/verify-email", { body: { email, otp: "999999" } });
    }
    const response = await call("/email-otp/verify-email", {
      body: { email, otp: lastCode(email) },
    });
    expect(await code(response)).toBe("TOO_MANY_ATTEMPTS");
  });

  it("has no password sign-up, OTP sign-in or other code routes", async () => {
    for (const path of [
      "/sign-up/email",
      "/sign-in/email-otp",
      "/email-otp/send-verification-otp",
      "/send-verification-email",
    ]) {
      const response = await call(path, { body: { email: "x@example.com", password: STRONG } });
      expect(response.status, path).toBe(404);
    }
  });
});

describe("sign-in is for existing accounts only", () => {
  it("emails no link to an unknown address, and says to register", async () => {
    const response = await call("/sign-in/magic-link", { body: { email: "stranger@example.com" } });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      code: "ACCOUNT_NOT_FOUND",
      message: "No account found for that email. Register first.",
    });
    expect(sentLinks).toEqual([]);
    expect(JSON.stringify(db.verification)).not.toContain("stranger@example.com");
  });

  it("emails a link to an existing account", async () => {
    await registerAndVerify("linker@example.com");
    const response = await call("/sign-in/magic-link", { body: { email: "Linker@example.com" } });
    expect(response.status).toBe(200);
    expect(sentLinks).toHaveLength(1);
  });

  it("makes an older unverified password account verify its address first", async () => {
    const ctx = await auth.$context;
    // as registered with a password before ADR-042: never verified
    const user = await ctx.internalAdapter.createUser(
      { email: "legacy@example.com", name: "Legacy", emailVerified: false },
      { method: "email-password" },
    );
    await ctx.internalAdapter.linkAccount({
      userId: user.id,
      providerId: "credential",
      accountId: user.id,
      password: await ctx.password.hash("old-weak-pw"),
    });

    const wrong = await call("/sign-in/email", {
      body: { email: "legacy@example.com", password: "not it" },
    });
    expect(wrong.status).toBe(401);
    expect(sentCodes).toEqual([]);

    const right = await call("/sign-in/email", {
      body: { email: "legacy@example.com", password: "old-weak-pw" },
    });
    expect(right.status).toBe(403);
    expect(await code(right)).toBe("EMAIL_NOT_VERIFIED");
    expect(cookiesOf(right)).not.toContain("session_token");
    expect(lastCode("legacy@example.com")).toMatch(/^\d{6}$/);

    const verified = await call("/email-otp/verify-email", {
      body: { email: "legacy@example.com", otp: lastCode("legacy@example.com") },
    });
    expect(verified.status).toBe(200);
    // nothing deleted: the old (weak) password keeps working
    const again = await call("/sign-in/email", {
      body: { email: "legacy@example.com", password: "old-weak-pw" },
    });
    expect(again.status).toBe(200);
  });
});

describe("password policy on the server", () => {
  it("refuses a weak first password and accepts a strong one (auth.api.setPassword)", async () => {
    const cookie = await registerAndVerify("setter@example.com");
    const headers = new Headers({ cookie, origin: ORIGIN });
    await expect(
      auth.api.setPassword({ body: { newPassword: "weakpassword" }, headers }),
    ).rejects.toMatchObject({ body: { code: "PASSWORD_TOO_WEAK" } });
    await expect(
      auth.api.setPassword({ body: { newPassword: "Setter#2026xyz" }, headers }),
    ).rejects.toMatchObject({ body: { code: "PASSWORD_TOO_WEAK" } }); // contains "setter"
    await expect(auth.api.setPassword({ body: { newPassword: STRONG }, headers })).resolves.toEqual(
      { status: true },
    );
    const signedIn = await call("/sign-in/email", {
      body: { email: "setter@example.com", password: STRONG },
    });
    expect(signedIn.status).toBe(200);

    const change = await call("/change-password", {
      cookie,
      body: { currentPassword: STRONG, newPassword: "short" },
    });
    expect(await code(change)).toBe("PASSWORD_TOO_WEAK");
  });

  it("refuses a weak password at reset, including one with the email's name in it", async () => {
    await registerAndVerify("resetter@example.com");
    await call("/request-password-reset", { body: { email: "resetter@example.com" } });
    const row = (db.verification as { identifier: string }[]).find((r) =>
      r.identifier.startsWith("reset-password:"),
    )!;
    const token = row.identifier.slice("reset-password:".length);
    const weak = await call("/reset-password", {
      body: { token, newPassword: "Resetter!2026" },
    });
    expect(await code(weak)).toBe("PASSWORD_TOO_WEAK");
    // the refused attempt did not use the token up
    const strong = await call("/reset-password", { body: { token, newPassword: STRONG } });
    expect(strong.status).toBe(200);
  });
});

describe("Google", () => {
  async function googleFlow(email: string, requestSignUp: boolean): Promise<Response> {
    googleUser.id = `google-${email}`;
    googleUser.email = email;
    const start = await call("/sign-in/social", {
      body: {
        provider: "google",
        callbackURL: "/",
        errorCallbackURL: "/sign-in",
        ...(requestSignUp ? { requestSignUp: true } : {}),
      },
    });
    expect(start.status).toBe(200);
    const { url } = (await start.json()) as { url: string };
    const state = new URL(url).searchParams.get("state");
    return call(`/callback/google?code=c&state=${state}`, { cookie: cookiesOf(start) });
  }

  it("refuses an unknown Google address on sign-in", async () => {
    const response = await googleFlow("new-google@example.com", false);
    expect(response.headers.get("location")).toMatch(/\/sign-in\?error=signup_disabled/);
    expect(users().some((u) => u.email === "new-google@example.com")).toBe(false);
  });

  it("creates the account from 'Sign up with Google'", async () => {
    const response = await googleFlow("new-google@example.com", true);
    expect(response.headers.get("location")).toBe("/");
    expect(users().find((u) => u.email === "new-google@example.com")).toMatchObject({
      emailVerified: true,
    });
  });

  it("links Google to an account verified at registration", async () => {
    await registerAndVerify("both@example.com");
    const response = await googleFlow("both@example.com", false);
    expect(response.headers.get("location")).toBe("/");
    const id = users().find((u) => u.email === "both@example.com")!.id;
    expect(
      accounts()
        .filter((a) => a.userId === id)
        .map((a) => a.providerId),
    ).toEqual(["google"]);
  });

  it("does not link Google to an unverified account", async () => {
    await call("/register/email", { body: { name: "Half", email: "half@example.com" } });
    const response = await googleFlow("half@example.com", false);
    expect(response.headers.get("location")).toMatch(/error=account_not_linked/);
  });
});

describe("profile and account deletion", () => {
  it("changes the name only, never the email or anything else", async () => {
    const cookie = await registerAndVerify("profile@example.com");
    const ok = await call("/update-user", { cookie, body: { name: "Ada King" } });
    expect(ok.status).toBe(200);
    expect(users().find((u) => u.email === "profile@example.com")).toMatchObject({
      name: "Ada King",
    });
    for (const body of [
      { email: "other@example.com" },
      { name: "Ada", image: "x" },
      { name: " " },
    ]) {
      const refused = await call("/update-user", { cookie, body });
      expect(await code(refused)).toBe("INVALID_PROFILE");
    }
    expect(
      (await call("/change-email", { cookie, body: { newEmail: "x@example.com" } })).status,
    ).toBe(404);
  });

  it("has no browser route to delete an account; the server deletes it with the password", async () => {
    const cookie = await registerAndVerify("leaver@example.com");
    const headers = new Headers({ cookie, origin: ORIGIN });
    await auth.api.setPassword({ body: { newPassword: STRONG }, headers });
    expect((await call("/delete-user", { cookie, body: { password: STRONG } })).status).toBe(404);

    await expect(
      auth.api.verifyPassword({ body: { password: "not it" }, headers }),
    ).rejects.toMatchObject({ body: { code: "INVALID_PASSWORD" } });
    await expect(auth.api.verifyPassword({ body: { password: STRONG }, headers })).resolves.toEqual(
      {
        status: true,
      },
    );

    const id = users().find((u) => u.email === "leaver@example.com")!.id;
    poolQuery.mockClear();
    await auth.api.deleteUser({ body: { password: STRONG }, headers });
    expect(users().some((u) => u.id === id)).toBe(false);
    expect(accounts().some((a) => a.userId === id)).toBe(false);
    expect((db.session as { userId: string }[]).some((row) => row.userId === id)).toBe(false);
    // its workspace and pending codes, links and tokens (identity.forgetUser)
    const statements = poolQuery.mock.calls.map(([sql, params]) => [String(sql), params]);
    expect(statements.some(([sql]) => /DELETE FROM organization/.test(String(sql)))).toBe(true);
    expect(statements).toContainEqual([
      expect.stringMatching(/DELETE FROM verification/),
      [id, "leaver@example.com"],
    ]);
  });
});
