import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as EnvModule from "@/lib/env";

const env = vi.hoisted(() => ({
  current: {
    EMAIL_TRANSPORT: "console",
    EMAIL_FROM: "IC <no-reply@ic.test>",
    RESEND_API_KEY: "re_key",
  } as Record<string, string>,
}));
vi.mock("@/lib/env", async (original) => ({
  ...(await original<typeof EnvModule>()),
  getEnv: () => env.current,
}));

import {
  EmailDeliveryError,
  escapeAttribute,
  isResendApiKey,
  renderOtpEmail,
  renderPasswordResetEmail,
  renderSignInEmail,
  sendOtpEmail,
  sendPasswordResetEmail,
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

describe("sendPasswordResetEmail", () => {
  const fetchMock = vi.fn();
  const message = { to: "ada@example.com", url: "https://app.test/api/auth/reset-password/t" };
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    delete env.current.EMAIL_RESET_FROM;
  });

  it("console transport prints the link and sends nothing", async () => {
    env.current.EMAIL_TRANSPORT = "console";
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await sendPasswordResetEmail(message);
    expect(warn.mock.calls.at(-1)?.[0]).toContain("Password reset link");
    expect(warn.mock.calls.at(-1)?.[0]).toContain(message.url);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends from EMAIL_RESET_FROM, the no-reply address", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    env.current.EMAIL_RESET_FROM = "IC <no-reply@ic.test>";
    env.current.EMAIL_FROM = "IC <login@ic.test>";
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await sendPasswordResetEmail(message);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.resend.com/emails");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer re_key");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({
      from: "IC <no-reply@ic.test>",
      to: ["ada@example.com"],
      subject: "Reset your InsightfulCell password",
    });
    expect(body.text).toContain(message.url);
  });

  it("sends from no-reply@ on EMAIL_FROM's domain when no reset sender is set", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    env.current.EMAIL_FROM = "IC <login@ic.test>";
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await sendPasswordResetEmail(message);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string).from).toBe("IC <no-reply@ic.test>");
  });

  it("failures never contain the link or the recipient", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockResolvedValue(new Response("{}", { status: 500 }));
    const error = await sendPasswordResetEmail({
      to: "ada@example.com",
      url: "https://app.test/api/auth/reset-password/secret",
    }).catch((e: Error) => e);
    expect(error).toBeInstanceOf(EmailDeliveryError);
    expect((error as Error).message).not.toMatch(/secret|ada@example\.com/);
  });
});

describe("renderPasswordResetEmail", () => {
  it("escapes the link for HTML and says how long it works", () => {
    const { subject, html, text } = renderPasswordResetEmail({
      url: 'https://a.test/?a=1&b="2"<x>',
    });
    expect(subject).toBe("Reset your InsightfulCell password");
    expect(html).toContain("a=1&amp;b=&quot;2&quot;&lt;x&gt;");
    expect(text).toContain('https://a.test/?a=1&b="2"<x>');
    expect(text).toContain("expires in 30 minutes");
  });
});

describe("escapeAttribute", () => {
  it("escapes what could end an attribute or start a tag", () => {
    expect(escapeAttribute(`a&b"c<d>e'`)).toBe("a&amp;b&quot;c&lt;d&gt;e'");
  });
});

describe("renderOtpEmail", () => {
  it("escapes the code for HTML, keeps it out of the subject, says it expires in 10 minutes", () => {
    const { subject, html, text } = renderOtpEmail({ otp: '12<b>"&' });
    expect(subject).toBe("Your InsightfulCell verification code");
    expect(subject).not.toContain("12");
    expect(html).toContain("12&lt;b&gt;&quot;&amp;");
    expect(html).not.toContain("<b>");
    expect(text).toContain('12<b>"&');
    expect(text).toContain("expires in 10 minutes");
    expect(text).toContain("If you didn't ask for this code, ignore this email");
  });
});

describe("sendOtpEmail", () => {
  const fetchMock = vi.fn();
  const message = { to: "ada@example.com", otp: "482915" };
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("console transport prints the code and sends nothing", async () => {
    env.current.EMAIL_TRANSPORT = "console";
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await sendOtpEmail(message);
    expect(warn.mock.calls.at(-1)?.[0]).toContain("Verification code for ada@example.com");
    expect(warn.mock.calls.at(-1)?.[0]).toContain("482915");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends from EMAIL_FROM, the login address", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    env.current.EMAIL_FROM = "IC <login@ic.test>";
    env.current.EMAIL_RESET_FROM = "IC <no-reply@ic.test>";
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await sendOtpEmail(message);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({ from: "IC <login@ic.test>", to: ["ada@example.com"] });
    expect(body.text).toContain("482915");
    delete env.current.EMAIL_RESET_FROM;
  });

  it("failures never contain the code or the recipient", async () => {
    env.current.EMAIL_TRANSPORT = "resend";
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ name: "invalid_api_key" }), { status: 401 }),
    );
    const error = await sendOtpEmail(message).catch((e: Error) => e);
    expect(error).toMatchObject({ reason: "invalid-key" });
    expect((error as Error).message).not.toMatch(/482915|ada@example\.com/);
  });
});
