import { describe, expect, it } from "vitest";
import { apiPublicUrl, parseEnv } from "@/lib/env";

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

  it("requires an API key for the resend transport", () => {
    expect(() => parseEnv({ ...valid, EMAIL_TRANSPORT: "resend" })).toThrow(/RESEND_API_KEY/);
  });

  describe("production guards", () => {
    const production = {
      ...valid,
      APP_ENV: "production",
      BETTER_AUTH_URL: "https://app.example.com",
      API_PUBLIC_URL: "https://api.example.com",
    };

    it("rejects the console email transport (it prints sign-in links)", () => {
      expect(() => parseEnv(production)).toThrow(/console transport/);
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
