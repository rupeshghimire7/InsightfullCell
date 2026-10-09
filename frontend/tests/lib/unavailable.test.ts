import { describe, expect, it } from "vitest";
import { isConnectionFailure, ServiceUnavailableError } from "@/lib/unavailable";

const withCode = (code: string, message = "boom") => Object.assign(new Error(message), { code });

describe("isConnectionFailure", () => {
  it.each(["ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "57P03", "08006", "28P01", "3D000", "42P01"])(
    "recognises %s",
    (code) => expect(isConnectionFailure(withCode(code))).toBe(true),
  );

  it("recognises node-postgres connection messages", () => {
    expect(isConnectionFailure(new Error("Connection terminated unexpectedly"))).toBe(true);
    expect(isConnectionFailure(new Error("timeout exceeded when trying to connect"))).toBe(true);
  });

  it("looks through causes and aggregate errors", () => {
    const wrapped = new Error("Failed to get session", { cause: withCode("ECONNREFUSED") });
    expect(isConnectionFailure(wrapped)).toBe(true);
    const dualStack = new AggregateError([withCode("ECONNREFUSED"), withCode("ECONNREFUSED")]);
    expect(isConnectionFailure(dualStack)).toBe(true);
  });

  it("does not mistake bugs or other database errors for an outage", () => {
    expect(isConnectionFailure(new TypeError("x is undefined"))).toBe(false);
    expect(isConnectionFailure(withCode("23505"))).toBe(false); // unique violation
    expect(isConnectionFailure(null)).toBe(false);
    expect(isConnectionFailure("ECONNREFUSED")).toBe(false);
  });

  it("stops on a cyclic cause chain", () => {
    const a: Error & { cause?: unknown } = new Error("a");
    a.cause = a;
    expect(isConnectionFailure(a)).toBe(false);
  });
});

describe("ServiceUnavailableError", () => {
  it("keeps the original error as its cause", () => {
    const cause = withCode("ECONNREFUSED");
    const error = new ServiceUnavailableError(undefined, { cause });
    expect(error.name).toBe("ServiceUnavailableError");
    expect(error.cause).toBe(cause);
  });
});
