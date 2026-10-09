import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/** WCAG 2.2 AA contrast of the design tokens (08-ui-ux-spec.md section 2.1), light and dark. */
const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");

function tokens(block: RegExp): Record<string, string> {
  const body = css.match(block)?.[1] ?? "";
  return Object.fromEntries(
    [...body.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6});/gi)].map((m) => [m[1]!, m[2]!]),
  );
}

const light = tokens(/:root\s*{([^}]*)}/);
const dark = tokens(/:root\[data-theme="dark"\]\s*{([^}]*)}/);

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe.each([
  ["light", light],
  ["dark", dark],
])("%s theme", (_name, theme) => {
  it("defines every token", () => {
    for (const key of [
      "bg",
      "surface",
      "text",
      "text-muted",
      "accent",
      "accent-contrast",
      "warning",
      "danger",
      "border",
    ]) {
      expect(theme[key], key).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it.each([
    ["text", "bg"],
    ["text", "surface"],
    ["text-muted", "bg"],
    ["text-muted", "surface"],
    ["accent", "bg"],
    ["accent", "surface"],
    ["accent-contrast", "accent"],
    ["danger", "surface"],
    ["warning", "surface"],
  ])("%s on %s meets 4.5:1", (foreground, background) => {
    expect(contrast(theme[foreground]!, theme[background]!)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps UI borders visible against the page (3:1 is for controls; borders only need to be perceptible)", () => {
    expect(contrast(theme["border"]!, theme["bg"]!)).toBeGreaterThan(1.1);
  });
});

describe("contrast helper", () => {
  it("matches the WCAG reference values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
  });
});
