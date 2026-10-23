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
    expect(team.map((m) => m.name)).toEqual(["Rupesh Ghimire", "Sandesh Prasad Paudel"]);
    for (const member of team) {
      expect(member.role).toBeTruthy();
      expect(member.linkedin).toMatch(/^https:\/\//);
      expect(member.email).toContain("@");
      expect(member.bio.length).toBeGreaterThan(0);
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
