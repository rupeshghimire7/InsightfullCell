"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "@/components/ui/icons";
import { THEME_COOKIE, type Theme } from "@/lib/theme";

const CHANGED = "ic-theme-change";
const DARK_QUERY = "(prefers-color-scheme: dark)";

/** The theme in effect: the person's choice on `<html data-theme>`, else the system setting. */
function currentTheme(): Theme {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
}

function subscribe(onChange: () => void) {
  const media = window.matchMedia?.(DARK_QUERY);
  media?.addEventListener("change", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    media?.removeEventListener("change", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/** Switches between the light and dark themes and remembers the choice for a year. */
export function ThemeToggle({ initial }: { initial?: Theme }) {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => initial ?? "light");
  const next: Theme = theme === "dark" ? "light" : "dark";

  function toggle() {
    document.documentElement.dataset.theme = next;
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${THEME_COOKIE}=${next}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
    window.dispatchEvent(new Event(CHANGED));
  }

  const Icon = theme === "dark" ? Sun : Moon;
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="press inline-flex size-9 shrink-0 items-center justify-center rounded-control border border-border bg-surface text-muted hover:bg-bg hover:text-text pointer-coarse:size-11"
    >
      <Icon aria-hidden="true" className="size-4" />
    </button>
  );
}
