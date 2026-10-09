import { describe, expect, it } from "vitest";
import { identityFromApiToken, TICKET_SECONDS, ticketClaims } from "@/lib/events-ticket";
import { isUuid } from "@/lib/ids";

function jwt(payload: unknown): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  return `${encode({ alg: "EdDSA" })}.${encode(payload)}.signature`;
}

describe("identityFromApiToken", () => {
  it("reads the identity claims of an API token", () => {
    const token = jwt({
      sub: "u1",
      org_id: "o1",
      role: "analyst",
      org_name: "Acme ~?>",
      aud: "api",
    });
    expect(identityFromApiToken(token)).toEqual({
      sub: "u1",
      org_id: "o1",
      role: "analyst",
      org_name: "Acme ~?>",
    });
  });

  it.each([
    ["no payload", "abc"],
    ["not base64 JSON", "a.!!!.c"],
    ["a JSON scalar", jwt(42)],
    ["no organisation", jwt({ sub: "u1", role: "owner" })],
    ["a numeric role", jwt({ sub: "u1", org_id: "o1", role: 3 })],
  ])("returns null for %s", (_name, token) => {
    expect(identityFromApiToken(token)).toBeNull();
  });
});

describe("ticketClaims", () => {
  it("repeats the identity, names one run and lives five minutes", () => {
    const claims = ticketClaims(
      { sub: "u1", org_id: "o1", role: "viewer" },
      "r1",
      "insightfulcell-events",
      1_000,
    );
    expect(claims).toEqual({
      sub: "u1",
      org_id: "o1",
      role: "viewer",
      run_id: "r1",
      aud: "insightfulcell-events",
      iat: 1_000,
      exp: 1_000 + TICKET_SECONDS,
    });
    expect(TICKET_SECONDS).toBe(300);
  });
});

describe("isUuid", () => {
  it("accepts UUIDs only", () => {
    expect(isUuid("01a120e9-cc81-7ec1-9428-29aea00bb693")).toBe(true);
    expect(isUuid("01A120E9-CC81-7EC1-9428-29AEA00BB693")).toBe(true);
    for (const bad of ["", "nope", "01a120e9-cc81-7ec1-9428-29aea00bb69", "../x"]) {
      expect(isUuid(bad)).toBe(false);
    }
  });
});
