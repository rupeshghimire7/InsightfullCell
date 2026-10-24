import { describe, expect, it } from "vitest";
import {
  emailName,
  passwordPolicyError,
  passwordProblems,
  PASSWORD_RULES,
} from "@/lib/password-policy";

const GOOD = "Analytical#Engine9";

describe("passwordProblems", () => {
  it("accepts a password that meets every rule", () => {
    expect(passwordProblems(GOOD, "ada@example.com")).toEqual([]);
  });

  it.each([
    ["length", "Ab1!xyzpq"], // 9 characters
    ["lowercase", "ANALYTICAL#ENGINE9"],
    ["uppercase", "analytical#engine9"],
    ["digit", "Analytical#Engine"],
    ["special", "AnalyticalEngine9"],
    ["no-spaces", "Analytical #Engine9"],
  ] as const)("refuses a password breaking only the %s rule", (rule, password) => {
    expect(passwordProblems(password)).toEqual([rule]);
  });

  it("counts characters, not UTF-16 units, and accepts letters beyond ASCII", () => {
    expect(passwordProblems("Émile#Zola1")).toEqual([]);
    // each emoji is one character (two UTF-16 units)
    expect(passwordProblems("Ab1!😀😀😀😀😀😀")).toEqual([]);
    expect(passwordProblems("Ab1!😀😀😀😀😀")).toEqual(["length"]);
  });

  it("refuses a password containing the email's name, whatever the case", () => {
    expect(passwordProblems("xAda.Lovelace#1", "ada.lovelace@example.com")).toEqual([
      "no-email-name",
    ]);
    expect(passwordProblems("ADA.LOVELACE#1z", "ada.lovelace@example.com")).toContain(
      "no-email-name",
    );
    // the domain is fine, and so is the name when the email is unknown
    expect(passwordProblems("Example.com#99", "ada.lovelace@example.com")).toEqual([]);
    expect(passwordProblems("xAda.Lovelace#1")).toEqual([]);
  });

  it("skips the email rule for very short names, which would refuse common words", () => {
    expect(emailName("jo@example.com")).toBeNull();
    expect(emailName("Ada@example.com")).toBe("ada");
    expect(passwordProblems("Jogging#Daily1", "jo@example.com")).toEqual([]);
  });
});

describe("passwordPolicyError", () => {
  it("names everything missing in one sentence, in checklist order", () => {
    expect(passwordPolicyError("abc")).toBe(
      "Choose a stronger password: at least 10 characters; an uppercase letter; a number; a symbol, like ! # or %.",
    );
  });

  it("is null for a good password and refuses one too long to hash", () => {
    expect(passwordPolicyError(GOOD)).toBeNull();
    expect(passwordPolicyError(`${GOOD}${"a".repeat(130)}`)).toBe("Use at most 128 characters.");
  });

  it("has one label per rule", () => {
    expect(new Set(PASSWORD_RULES.map((r) => r.label)).size).toBe(PASSWORD_RULES.length);
  });
});
