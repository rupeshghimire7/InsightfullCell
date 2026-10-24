"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { List, SignOut } from "@/components/ui/icons";

const ITEM =
  "flex min-h-11 w-full items-center rounded-control px-3 text-left text-sm font-medium hover:bg-bg focus-visible:bg-bg";

/**
 * The menu beside "Sign out": about, contact, licence, profile and data & privacy pages. A native disclosure, as
 * the report's download menu: it closes after a choice, on Escape and on a click elsewhere. On
 * narrow screens it also holds "Sign out", whose own button is hidden there to fit the header.
 */
export function SiteMenu({
  home,
  onSignOut,
  signingOut,
}: {
  home: string;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const menu = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const close = (event: Event) => {
      const element = menu.current;
      if (!element?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== "Escape") return;
        element.open = false;
        element.querySelector("summary")?.focus();
        return;
      }
      if (!element.contains(event.target as Node)) element.open = false;
    };
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", close);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("pointerdown", close);
    };
  }, []);

  const shut = () => {
    if (menu.current) menu.current.open = false;
  };
  const pages = [
    { href: `${home}/about`, label: "About InsightfulCell" },
    { href: `${home}/contact`, label: "Contact" },
    { href: `${home}/license`, label: "License" },
    { href: `${home}/profile`, label: "Profile" },
    { href: `${home}/privacy`, label: "Data & privacy" },
  ];

  return (
    <details ref={menu} className="relative">
      <summary
        aria-label="Menu"
        className="press inline-flex size-9 cursor-pointer list-none items-center justify-center rounded-control border border-border bg-surface text-muted hover:bg-bg hover:text-text pointer-coarse:size-11 [&::-webkit-details-marker]:hidden"
      >
        <List aria-hidden="true" className="size-4" />
      </summary>
      <nav
        aria-label="More"
        className="absolute right-0 z-30 mt-1 w-52 rounded-panel border border-border bg-surface p-1 shadow-lg"
      >
        <ul>
          {pages.map((page) => (
            <li key={page.href}>
              <Link href={page.href} onClick={shut} className={ITEM}>
                {page.label}
              </Link>
            </li>
          ))}
          <li className="sm:hidden">
            <button
              type="button"
              onClick={onSignOut}
              disabled={signingOut}
              className={`${ITEM} gap-2 border-t border-border`}
            >
              <SignOut aria-hidden="true" className="size-4" />
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </li>
        </ul>
      </nav>
    </details>
  );
}
