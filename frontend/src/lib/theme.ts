/**
 * The colour theme a person picked with the header toggle. Stored in a cookie so the server can
 * render `<html data-theme>` (globals.css) before any script runs: no flash of the other theme.
 * No cookie means "follow the system setting".
 */
export type Theme = "light" | "dark";

export const THEME_COOKIE = "ic_theme";

export function parseTheme(value: string | undefined): Theme | undefined {
  return value === "light" || value === "dark" ? value : undefined;
}
