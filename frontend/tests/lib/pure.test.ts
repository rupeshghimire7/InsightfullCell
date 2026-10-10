import { describe, expect, it } from "vitest";
import {
  FORWARDED_REQUEST_HEADERS,
  FORWARDED_RESPONSE_HEADERS,
  isTrustedOrigin,
  resolveBackendPath,
} from "@/lib/backend-policy";
import { buildCsp } from "@/lib/csp";
import { describeError, ProblemError, readProblem } from "@/lib/problem";
import { effectiveRole } from "@/lib/roles";
import { formatBytes, formatCount, formatPercent, formatUtc } from "@/lib/format";
import { renderSignInEmail } from "@/lib/email";
import { workspaceName, workspaceSlug } from "@/lib/identity";
import { parseEnv } from "@/lib/env";
import { googleSignIn, linkDelivery } from "@/lib/sign-in-options";

describe("effectiveRole", () => {
  it.each([
    ["owner", "owner"],
    ["viewer,admin", "admin"],
    ["analyst, viewer", "analyst"],
    ["viewer", "viewer"],
    ["member", "viewer"],
    ["", "viewer"],
    [null, "viewer"],
    ["OWNER", "viewer"],
  ])("%j -> %s", (stored, expected) => {
    expect(effectiveRole(stored)).toBe(expected);
  });
});

describe("resolveBackendPath", () => {
  it.each([
    [["v1", "me"], "v1/me"],
    [["v1", "uploads"], "v1/uploads"],
    [["v1", "uploads", "abc:complete"], "v1/uploads/abc:complete"],
    [
      ["v1", "datasets", "0198d8f0-0000-7000-8000-000000000000"],
      "v1/datasets/0198d8f0-0000-7000-8000-000000000000",
    ],
    [["v1", "runs"], "v1/runs"],
    [["v1", "runs", "r1"], "v1/runs/r1"],
    [["v1", "runs", "r1:cancel"], "v1/runs/r1:cancel"],
    [["v1", "runs", "r1:retry"], "v1/runs/r1:retry"],
  ])("allows %j", (segments, expected) => {
    expect(resolveBackendPath(segments)).toBe(expected);
  });

  it.each([
    [[]],
    [["healthz"]],
    [["readyz"]],
    [["docs"]],
    [["openapi.json"]],
    [["v1", "admin"]],
    [["v1", "uploads", ".."]],
    [["v1", "uploads", "%2e%2e"]],
    [["v1", "uploads", "a/b"]],
    [["v1", "uploads", "a?x=1"]],
    [["v1", "uploads", "a", "b", "c"]],
    [["v1uploads"]],
    [["v1", "meta"]],
    // Event streams go to the API directly with a ticket; never held open by this proxy.
    [["v1", "runs", "r1", "events"]],
    [["v1", "runsx"]],
  ])("rejects %j", (segments) => {
    expect(resolveBackendPath(segments)).toBeNull();
  });

  it("does not forward cookies or authorization from the browser", () => {
    expect(FORWARDED_REQUEST_HEADERS).not.toContain("cookie");
    expect(FORWARDED_REQUEST_HEADERS).not.toContain("authorization");
  });

  it("forwards the conditional-request headers runs use", () => {
    expect(FORWARDED_REQUEST_HEADERS).toContain("if-match");
    expect(FORWARDED_RESPONSE_HEADERS).toEqual(expect.arrayContaining(["etag", "location"]));
  });
});

describe("isTrustedOrigin", () => {
  const app = "https://app.example.com";
  it("lets safe methods through without an origin", () => {
    expect(isTrustedOrigin("GET", null, app)).toBe(true);
  });
  it("requires the exact app origin for mutations", () => {
    expect(isTrustedOrigin("POST", app, app)).toBe(true);
    expect(isTrustedOrigin("POST", null, app)).toBe(false);
    expect(isTrustedOrigin("POST", "https://evil.example", app)).toBe(false);
    expect(isTrustedOrigin("POST", `${app}.evil.example`, app)).toBe(false);
  });
});

describe("buildCsp", () => {
  const options = { nonce: "abc123", storageOrigin: "https://bucket.s3.amazonaws.com/some/path" };

  it("is nonce-based and never allows inline or eval scripts in production", () => {
    const csp = buildCsp({ ...options, upgradeInsecureRequests: true });
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic' 'wasm-unsafe-eval'");
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-(inline|eval)'/);
    expect(csp).toContain("upgrade-insecure-requests");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
  });

  it("allows only the storage origin (not its path) to receive uploads", () => {
    expect(buildCsp(options)).toContain("connect-src 'self' https://bucket.s3.amazonaws.com;");
  });

  it("lets the browser reach the API's origin for live progress", () => {
    expect(buildCsp({ ...options, apiOrigin: "https://api.example.com/v1" })).toContain(
      "connect-src 'self' https://bucket.s3.amazonaws.com https://api.example.com;",
    );
  });

  it("relaxes only what hot reload needs in development", () => {
    const csp = buildCsp({ ...options, development: true });
    expect(csp).toContain("'unsafe-eval'");
    expect(csp).not.toContain("upgrade-insecure-requests");
    expect(buildCsp(options)).not.toContain("upgrade-insecure-requests");
  });
});

describe("problems", () => {
  it("parses problem+json bodies", async () => {
    const response = new Response(
      JSON.stringify({ title: "Nope", status: 422, code: "invalid-request", hint: "Fix it" }),
      { status: 422 },
    );
    expect(await readProblem(response)).toMatchObject({ code: "invalid-request", hint: "Fix it" });
  });

  it("never throws on non-JSON bodies", async () => {
    const problem = await readProblem(new Response("<html>bad gateway</html>", { status: 502 }));
    expect(problem).toMatchObject({ status: 502, code: "unknown" });
  });

  it("maps errors to readable problems", () => {
    expect(describeError(new TypeError("fetch failed")).code).toBe("network");
    expect(describeError(new DOMException("x", "AbortError")).code).toBe("cancelled");
    expect(describeError(new Error("boom")).code).toBe("unknown");
    const problem = { title: "T", status: 409, code: "upload-expired" };
    expect(describeError(new ProblemError(problem))).toBe(problem);
  });
});

describe("format", () => {
  it("formats sizes with a non-breaking space", () => {
    expect(formatBytes(0, "en-US")).toBe("0 B");
    expect(formatBytes(1_500, "en-US")).toBe("1.5 KB");
    expect(formatBytes(250_000_000, "en-US")).toBe("250 MB");
  });
  it("formats percentages and counts through Intl", () => {
    expect(formatPercent(0.456, "en-US")).toBe("46%");
    expect(formatPercent(5, "en-US")).toBe("100%");
    expect(formatCount(1234567, "en-US")).toBe("1,234,567");
  });
  it("formats a server-safe UTC time the same in every locale and zone", () => {
    expect(formatUtc("2026-10-10T13:45:12.345+13:45")).toBe("2026-10-10 00:00\u00a0UTC");
    expect(formatUtc("not a date")).toBe("not a date");
  });
});

describe("sign-in email", () => {
  it("includes the link, the expiry and escapes HTML", () => {
    const { text, html, subject } = renderSignInEmail({ url: "https://x.test/v?a=1&b=2" });
    expect(subject).toMatch(/sign-in link/);
    expect(text).toContain("https://x.test/v?a=1&b=2");
    expect(text).toContain("10 minutes");
    expect(html).toContain("a=1&amp;b=2");
  });
});

describe("workspace naming", () => {
  it("derives a stable slug from the user id", () => {
    expect(workspaceSlug("0198d8f0-1111-7222-8333-444455556666")).toBe("ws-0198d8f01111");
  });
  it("names the workspace after the email's local part", () => {
    expect(workspaceName("ada@lovelace.dev")).toBe("ada\u2019s workspace");
    expect(workspaceName(undefined)).toBe("My workspace");
  });
});

describe("sign-in options", () => {
  const env = (extra: Record<string, string> = {}) =>
    parseEnv({
      BETTER_AUTH_URL: "http://localhost:3000",
      BETTER_AUTH_SECRET: "x".repeat(32),
      AUTH_DATABASE_URL: "postgresql://localhost/db",
      API_BASE_URL: "http://localhost:8000",
      S3_PUBLIC_ORIGIN: "http://localhost:9000",
      ...extra,
    });
  const resend = { EMAIL_TRANSPORT: "resend", RESEND_API_KEY: "k" };
  const on = { EMAIL_LINK_SIGN_IN: "on" };

  it("says where sign-in links go, and that they are off by default", () => {
    expect(linkDelivery(env())).toBe("off");
    expect(linkDelivery(env(resend))).toBe("off");
    expect(linkDelivery(env(on))).toBe("log");
    expect(linkDelivery(env({ ...on, ...resend }))).toBe("resend-test"); // onboarding@resend.dev
    expect(linkDelivery(env({ ...on, ...resend, EMAIL_FROM: "Cell <login@cell.example>" }))).toBe(
      "email",
    );
  });

  it("offers Google when configured, explains setup in development, hides it in production", () => {
    expect(googleSignIn(env({ GOOGLE_CLIENT_ID: "id", GOOGLE_CLIENT_SECRET: "s" }))).toBe("on");
    expect(googleSignIn(env())).toBe("setup-needed");
    expect(
      googleSignIn(
        env({
          ...resend,
          APP_ENV: "production",
          BETTER_AUTH_URL: "https://cell.example",
          API_PUBLIC_URL: "https://api.cell.example",
        }),
      ),
    ).toBe("off");
  });
});
