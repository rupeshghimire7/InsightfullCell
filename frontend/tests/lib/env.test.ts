import { describe, expect, it } from "vitest";
import {
  apiPublicUrl,
  appOrigins,
  emailLinkAllowed,
  emailLinkAllowList,
  parseEnv,
  resetSender,
} from "@/lib/env";

const valid = {
  NODE_ENV: "development",
  BETTER_AUTH_URL: "http://localhost:3000",
  BETTER_AUTH_SECRET: "x".repeat(32),
  AUTH_DATABASE_URL: "postgresql://localhost/db",
  API_BASE_URL: "http://localhost:8000",
  S3_PUBLIC_ORIGIN: "http://localhost:9000",
};

describe("parseEnv", () => {
  it("accepts a minimal development configuration and applies defaults", () => {
    const env = parseEnv(valid);
    expect(env.EMAIL_TRANSPORT).toBe("console");
    expect(env.AUTH_JWT_AUDIENCE).toBe("insightfulcell-api");
  });

  it("names every invalid variable", () => {
    expect(() => parseEnv({ ...valid, BETTER_AUTH_SECRET: "short", API_BASE_URL: "nope" })).toThrow(
      /BETTER_AUTH_SECRET[\s\S]*API_BASE_URL/,
    );
  });

  it("requires Google credentials as a pair", () => {
    expect(() => parseEnv({ ...valid, GOOGLE_CLIENT_ID: "id" })).toThrow(/GOOGLE_CLIENT_SECRET/);
    expect(parseEnv({ ...valid, GOOGLE_CLIENT_ID: "id", GOOGLE_CLIENT_SECRET: "s" })).toBeTruthy();
  });

  it("keeps password reset off by default, and reset emails come from EMAIL_FROM", () => {
    const env = parseEnv(valid);
    expect(env.PASSWORD_RESET).toBe("off");
    expect(resetSender(env)).toBe(env.EMAIL_FROM);
    expect(() => parseEnv({ ...valid, PASSWORD_RESET: "yes" })).toThrow(/PASSWORD_RESET/);
  });

  it("sends reset emails from EMAIL_RESET_FROM when set, treating blank as unset", () => {
    const sender = "IC <no-reply@ic.example.com>";
    expect(resetSender(parseEnv({ ...valid, EMAIL_RESET_FROM: sender }))).toBe(sender);
    const blank = parseEnv({ ...valid, EMAIL_RESET_FROM: " " });
    expect(resetSender(blank)).toBe(blank.EMAIL_FROM);
  });

  it("requires an API key for the resend transport", () => {
    expect(() => parseEnv({ ...valid, EMAIL_TRANSPORT: "resend" })).toThrow(/RESEND_API_KEY/);
  });

  it("reads the sign-in link allow-list, and refuses anything that is not an address", () => {
    const env = parseEnv({ ...valid, EMAIL_LINK_ALLOWED_EMAILS: " Owner@Example.com , b@c.io " });
    expect(emailLinkAllowList(env)).toEqual(["owner@example.com", "b@c.io"]);
    expect(emailLinkAllowed("OWNER@example.com ", env)).toBe(true);
    expect(emailLinkAllowed("someone@example.com", env)).toBe(false);
    expect(emailLinkAllowed("anyone@example.com", parseEnv(valid))).toBe(true);
    expect(() => parseEnv({ ...valid, EMAIL_LINK_ALLOWED_EMAILS: "not-an-email" })).toThrow(
      /EMAIL_LINK_ALLOWED_EMAILS/,
    );
  });

  it("takes an admin key of 32 characters or more, and treats blank as off", () => {
    expect(parseEnv({ ...valid, ADMIN_DASHBOARD_KEY: "  " }).ADMIN_DASHBOARD_KEY).toBeUndefined();
    expect(parseEnv({ ...valid, ADMIN_DASHBOARD_KEY: "k".repeat(32) }).ADMIN_DASHBOARD_KEY).toBe(
      "k".repeat(32),
    );
    expect(() => parseEnv({ ...valid, ADMIN_DASHBOARD_KEY: "short" })).toThrow(
      /ADMIN_DASHBOARD_KEY/,
    );
    expect(parseEnv(valid).ADMIN_SESSION_MINUTES).toBe(30);
  });

  describe("production guards", () => {
    const production = {
      ...valid,
      APP_ENV: "production",
      BETTER_AUTH_URL: "https://app.example.com",
      API_PUBLIC_URL: "https://api.example.com",
      S3_PUBLIC_ORIGIN: "https://bucket.s3.eu-west-1.amazonaws.com",
    };

    it("rejects the console email transport when sign-in links are on (it prints them)", () => {
      expect(() => parseEnv({ ...production, EMAIL_LINK_SIGN_IN: "on" })).toThrow(
        /console transport/,
      );
    });
    it("needs the allow-list while links go out from Resend's test sender", () => {
      const resend = {
        ...production,
        EMAIL_LINK_SIGN_IN: "on",
        EMAIL_TRANSPORT: "resend",
        RESEND_API_KEY: "k",
      };
      expect(() => parseEnv(resend)).toThrow(/EMAIL_LINK_ALLOWED_EMAILS/);
      expect(parseEnv({ ...resend, EMAIL_LINK_ALLOWED_EMAILS: "owner@example.com" })).toBeTruthy();
    });
    it("rejects the console transport when password reset is on (it prints the links)", () => {
      expect(() => parseEnv({ ...production, PASSWORD_RESET: "on" })).toThrow(
        /EMAIL_TRANSPORT: The console transport prints reset links/,
      );
    });
    it("rejects Resend's test sender for reset emails, which reaches the owner only", () => {
      const resend = { ...production, EMAIL_TRANSPORT: "resend", RESEND_API_KEY: "k" };
      expect(() => parseEnv({ ...resend, PASSWORD_RESET: "on" })).toThrow(/EMAIL_RESET_FROM/);
      expect(() =>
        parseEnv({
          ...resend,
          PASSWORD_RESET: "on",
          EMAIL_FROM: "IC <login@ic.example.com>",
          EMAIL_RESET_FROM: "IC <onboarding@resend.dev>",
        }),
      ).toThrow(/EMAIL_RESET_FROM/);
      const ok = parseEnv({
        ...resend,
        PASSWORD_RESET: "on",
        EMAIL_RESET_FROM: "IC <no-reply@ic.example.com>",
      });
      expect(ok.PASSWORD_RESET).toBe("on");
    });
    it("allows it while sign-in links are off (the default): nothing is sent", () => {
      expect(parseEnv(production).EMAIL_LINK_SIGN_IN).toBe("off");
    });
    it("accepts only on or off for sign-in links", () => {
      expect(() => parseEnv({ ...production, EMAIL_LINK_SIGN_IN: "yes" })).toThrow(
        /EMAIL_LINK_SIGN_IN/,
      );
    });
    it("rejects a non-https public URL", () => {
      expect(() =>
        parseEnv({
          ...production,
          BETTER_AUTH_URL: "http://app.example.com",
          EMAIL_TRANSPORT: "resend",
          RESEND_API_KEY: "k",
        }),
      ).toThrow(/https/);
    });
    it("rejects a non-https API address for browsers", () => {
      expect(() =>
        parseEnv({
          ...production,
          API_PUBLIC_URL: undefined,
          EMAIL_TRANSPORT: "resend",
          RESEND_API_KEY: "k",
        }),
      ).toThrow(/API_PUBLIC_URL: Must be https/);
    });
    it("rejects browser-facing addresses on localhost", () => {
      const problems = () =>
        parseEnv({
          ...production,
          BETTER_AUTH_URL: "https://localhost:3000",
          S3_PUBLIC_ORIGIN: "https://127.0.0.1:9000",
          EMAIL_TRANSPORT: "resend",
          RESEND_API_KEY: "k",
        });
      expect(problems).toThrow(/BETTER_AUTH_URL: Points at localhost/);
      expect(problems).toThrow(/S3_PUBLIC_ORIGIN: Points at localhost/);
    });
    it("rejects a non-https storage origin", () => {
      expect(() =>
        parseEnv({
          ...production,
          S3_PUBLIC_ORIGIN: "http://bucket.example.com",
          EMAIL_TRANSPORT: "resend",
          RESEND_API_KEY: "k",
        }),
      ).toThrow(/S3_PUBLIC_ORIGIN: Must be https/);
    });
    it("accepts a correct production configuration", () => {
      expect(
        parseEnv({ ...production, EMAIL_TRANSPORT: "resend", RESEND_API_KEY: "k" }).APP_ENV,
      ).toBe("production");
    });
  });
});

describe("live-progress settings", () => {
  it("defaults the browser-facing API address to the server one", () => {
    const env = parseEnv(valid);
    expect(apiPublicUrl(env)).toBe(env.API_BASE_URL.replace(/\/+$/, ""));
    expect(apiPublicUrl(parseEnv({ ...valid, API_PUBLIC_URL: "https://api.test/" }))).toBe(
      "https://api.test",
    );
  });

  it("refuses an events audience equal to the API audience", () => {
    expect(() => parseEnv({ ...valid, AUTH_EVENTS_AUDIENCE: "insightfulcell-api" })).toThrow(
      /AUTH_EVENTS_AUDIENCE/,
    );
  });
});

describe("trusted origins", () => {
  it("serves BETTER_AUTH_URL alone by default", () => {
    expect(appOrigins(parseEnv(valid))).toEqual(["http://localhost:3000"]);
  });

  it("adds the listed origins, normalised and without duplicates", () => {
    const env = parseEnv({
      ...valid,
      AUTH_TRUSTED_ORIGINS:
        " https://abcd.ngrok-free.app/ ,http://localhost:3000,http://192.168.1.5:3000",
    });
    expect(appOrigins(env)).toEqual([
      "http://localhost:3000",
      "https://abcd.ngrok-free.app",
      "http://192.168.1.5:3000",
    ]);
  });

  it("refuses something that is not a bare origin", () => {
    for (const bad of [
      "abcd.ngrok-free.app",
      "https://app.example.com/sign-in",
      "ftp://x.example",
    ]) {
      expect(() => parseEnv({ ...valid, AUTH_TRUSTED_ORIGINS: bad })).toThrow(
        /AUTH_TRUSTED_ORIGINS: .* is not an origin/,
      );
    }
  });

  it("refuses http origins next to an https app, whose cookies are Secure", () => {
    expect(() =>
      parseEnv({
        ...valid,
        BETTER_AUTH_URL: "https://app.example.com",
        AUTH_TRUSTED_ORIGINS: "http://192.168.1.5:3000",
      }),
    ).toThrow(/must be https/);
  });
});
