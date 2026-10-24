import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseTeam } from "@/lib/team";
import { parseTheme } from "@/lib/theme";

describe("parseTeam", () => {
  it("reads name, role, links and bio paragraphs, ignoring comments", () => {
    const team = parseTeam(`<!-- ## Not a person -->

## Ada Lovelace

Role: Analyst
LinkedIn: https://www.linkedin.com/in/ada
Email: ada@example.com

First paragraph
continues here.

Second paragraph.

## Grace Hopper

Role: Engineer
LinkedIn:
Email: not-an-email

Bio.
`);
    expect(team).toEqual([
      {
        name: "Ada Lovelace",
        role: "Analyst",
        linkedin: "https://www.linkedin.com/in/ada",
        email: "ada@example.com",
        bio: ["First paragraph continues here.", "Second paragraph."],
      },
      { name: "Grace Hopper", role: "Engineer", bio: ["Bio."] },
    ]);
  });

  it("drops a link that is not https", () => {
    const [member] = parseTeam("## A\n\nLinkedIn: javascript:alert(1)\n\nBio.");
    expect(member?.linkedin).toBeUndefined();
  });

  it("parses the shipped team file", () => {
    const file = readFileSync(path.join(process.cwd(), "public/content/team.md"), "utf8");
    const team = parseTeam(file);
    expect(team.map((m) => m.name)).toEqual(["Rupesh Ghimire", "Sandesh Paudel"]);
    for (const member of team) {
      expect(member.role).toBeTruthy();
      expect(member.linkedin).toMatch(/^https:\/\/www\.linkedin\.com\/in\//);
      expect(member.email).toMatch(/@gmail\.com$/);
      expect(member.university).toBe("Charles Darwin University");
      expect(member.program).toBe("Masters of Data Science");
      expect(member.studentId).toMatch(/^s\d{6}$/);
      expect(member.bio).toHaveLength(1);
    }
  });
});

describe("parseTheme", () => {
  it("accepts only light or dark", () => {
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("blue")).toBeUndefined();
    expect(parseTheme(undefined)).toBeUndefined();
  });
});

describe("parseTeam: university details", () => {
  it("reads University, Program and Student ID", () => {
    const [member] = parseTeam(`## Ada Lovelace

Role: Lead
University: Charles Darwin University
Program: Masters of Data Science
Student ID: s123456
`);
    expect(member).toMatchObject({
      name: "Ada Lovelace",
      role: "Lead",
      university: "Charles Darwin University",
      program: "Masters of Data Science",
      studentId: "s123456",
      bio: [],
    });
  });
});

describe("parseTeam: the Bio field", () => {
  it("reads Bio: in any case and order, with blank lines between fields", () => {
    const [member] = parseTeam(`## Ada Lovelace

Role: Lead

BIO: Writes the engine.

LinkedIn: https://www.linkedin.com/in/ada
Email: ada@example.com
`);
    expect(member).toMatchObject({
      role: "Lead",
      bio: ["Writes the engine."],
      linkedin: "https://www.linkedin.com/in/ada",
      email: "ada@example.com",
    });
  });
});
