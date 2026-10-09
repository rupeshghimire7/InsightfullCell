"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

/** A navigation link that marks itself as the current page for its section. */
export function NavLink({
  href,
  sections = [],
  children,
}: {
  href: string;
  /** Other path prefixes that belong to this link's section. */
  sections?: string[];
  children: ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const current =
    pathname === href || sections.some((prefix) => pathname.startsWith(prefix))
      ? "page"
      : undefined;
  return (
    <Link
      href={href}
      aria-current={current}
      className="press inline-flex min-h-9 items-center rounded-control px-3 text-sm font-medium text-muted hover:bg-bg hover:text-text aria-[current=page]:bg-accent/10 aria-[current=page]:text-text pointer-coarse:min-h-11"
    >
      {children}
    </Link>
  );
}
