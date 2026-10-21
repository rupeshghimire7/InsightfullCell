import type { Pool } from "pg";
import { afterEach, describe, expect, it, vi } from "vitest";
import type * as EmailModule from "@/lib/email";

const sendSignInEmail = vi.fn();
vi.mock("@/lib/email", async (original) => ({
  ...(await original<typeof EmailModule>()),
  sendSignInEmail: (...args: unknown[]) => sendSignInEmail(...args),
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

  it("checks the database on every request, so signing out ends access at once", async () => {
    expect((await options()).session.cookieCache).toEqual({ enabled: false });
  });
});
