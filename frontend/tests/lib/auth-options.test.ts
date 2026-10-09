import { describe, expect, it } from "vitest";
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

  it("is refused once a verified domain sends email for real", () => {
    expect(() => userResendKey({ resendApiKey: KEY }, "email")).toThrow(
      expect.objectContaining({
        body: expect.objectContaining({ code: "RESEND_KEY_NOT_ACCEPTED" }),
      }),
    );
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
