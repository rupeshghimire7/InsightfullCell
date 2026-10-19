import type { Pool } from "pg";
import { afterEach, describe, expect, it, vi } from "vitest";
import { userResendKey } from "@/lib/auth-options";

const KEY = "re_test_0123456789abcdef";

describe("userResendKey", () => {
  it("is absent when the tester typed nothing", () => {
    expect(userResendKey(undefined, "log")).toBeUndefined();
    expect(userResendKey({}, "resend-test")).toBeUndefined();
    expect(userResendKey({ resendApiKey: "" }, "log")).toBeUndefined();
  });

  it("is accepted in both testing modes", () => {
    expect(userResendKey({ resendApiKey: KEY }, "log")).toBe(KEY);
    expect(userResendKey({ resendApiKey: KEY }, "resend-test")).toBe(KEY);
  });

  it("is refused once a verified domain sends email for real, or links are off", () => {
    for (const delivery of ["email", "off"] as const) {
      expect(() => userResendKey({ resendApiKey: KEY }, delivery)).toThrow(
        expect.objectContaining({
          body: expect.objectContaining({ code: "RESEND_KEY_NOT_ACCEPTED" }),
        }),
      );
    }
  });

  it("is refused when it is not shaped like a Resend key, without echoing it", () => {
    for (const bad of ["nope", 42, "re_\r\nInjected: header", { nested: KEY }]) {
      let caught: unknown;
      try {
        userResendKey({ resendApiKey: bad }, "log");
      } catch (error) {
        caught = error;
      }
      expect(caught).toMatchObject({ body: { code: "RESEND_KEY_INVALID" } });
      expect(String((caught as Error).message)).not.toContain("Injected");
    }
  });
});

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

  it("offers no sign-in link endpoint while links are off (the default)", async () => {
    expect(await pluginIds({})).not.toContain("magic-link");
  });

  it("offers it once switched on", async () => {
    expect(await pluginIds({ EMAIL_LINK_SIGN_IN: "on" })).toContain("magic-link");
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

  it("checks the database on every request, so signing out ends access at once", async () => {
    expect((await options()).session.cookieCache).toEqual({ enabled: false });
  });
});
