import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const env = vi.hoisted(() => ({
  current: {
    EMAIL_TRANSPORT: "console",
    EMAIL_FROM: "IC <no-reply@ic.test>",
    RESEND_API_KEY: "re_key",
  } as Record<string, string>,
}));
vi.mock("@/lib/env", () => ({ getEnv: () => env.current }));

import {
  EmailDeliveryError,
  isResendApiKey,
  renderSignInEmail,
  sendSignInEmail,
} from "@/lib/email";

describe("sendSignInEmail", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("console transport prints the link and sends nothing", async () => {
    env.current.EMAIL_TRANSPORT = "console";
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await sendSignInEmail({ to: "ada@example.com", url: "https://app.test/verify?token=t" });
    expect(warn.mock.calls[0]?.[0]).toContain("https://app.test/verify?token=t");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("resend transport posts the message with the API key", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await sendSignInEmail({ to: "ada@example.com", url: "https://app.test/verify?token=t" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.resend.com/emails");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer re_key");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({ from: "IC <no-reply@ic.test>", to: ["ada@example.com"] });
    expect(body.text).toContain("https://app.test/verify?token=t");
  });

  it("failure messages never contain the link or the recipient", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockResolvedValue(new Response("{}", { status: 422 }));
    const error = await sendSignInEmail({
      to: "ada@example.com",
      url: "https://app.test/v?token=secret",
    }).catch((e: Error) => e);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toContain("422");
    expect((error as Error).message).not.toMatch(/secret|ada@example\.com/);
  });

  it("a tester's own key is used once, from Resend's test sender, even with the console transport", async () => {
    env.current.EMAIL_TRANSPORT = "console";
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await sendSignInEmail(
      { to: "ada@example.com", url: "https://app.test/verify?token=t" },
      { apiKey: "re_tester_key_123" },
    );
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer re_tester_key_123");
    expect(JSON.parse(init.body as string).from).toBe("InsightfulCell <onboarding@resend.dev>");
    expect(warn).not.toHaveBeenCalled(); // the link is not printed when it was emailed
  });

  it.each([
    [403, { name: "invalid_api_key" }, "invalid-key"],
    [401, { name: "restricted_api_key" }, "invalid-key"],
    [403, { name: "validation_error" }, "recipient-not-allowed"],
    [429, { name: "rate_limit_exceeded" }, "rate-limited"],
    [500, {}, "failed"],
  ])("classifies Resend HTTP %i %o as %s", async (status, body, reason) => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status }));
    const error = await sendSignInEmail({ to: "a@b.co", url: "https://x.test" }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(EmailDeliveryError);
    expect((error as EmailDeliveryError).reason).toBe(reason);
  });

  it("reports a network failure without leaking the key", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const error = await sendSignInEmail(
      { to: "a@b.co", url: "https://x.test" },
      { apiKey: "re_secret_key_9999" },
    ).catch((e: Error) => e);
    expect((error as EmailDeliveryError).reason).toBe("failed");
    expect((error as Error).message).not.toContain("re_secret_key_9999");
  });
});

describe("isResendApiKey", () => {
  it("accepts Resend-shaped keys only", () => {
    expect(isResendApiKey("re_123456789_abcdefgh")).toBe(true);
    for (const bad of [
      "",
      "re_short",
      "sk_live_123456789",
      "re_has space_1234",
      "re_x\r\nX: y12345",
      42,
    ]) {
      expect(isResendApiKey(bad)).toBe(false);
    }
  });
});

describe("renderSignInEmail", () => {
  it("escapes the link for HTML", () => {
    const { html, text } = renderSignInEmail({ url: 'https://a.test/?a=1&b="2"<x>' });
    expect(html).toContain("a=1&amp;b=&quot;2&quot;&lt;x&gt;");
    expect(text).toContain('https://a.test/?a=1&b="2"<x>');
  });
});
