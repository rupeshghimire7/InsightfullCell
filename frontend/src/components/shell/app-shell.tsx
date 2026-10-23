import Link from "next/link";
import type { ReactNode } from "react";
import { NavLink } from "@/components/shell/nav-link";
import { UserMenu } from "@/components/shell/user-menu";
import { LogoMark } from "@/components/ui/brand";
import type { Theme } from "@/lib/theme";

/** Global chrome: skip link, header landmark, navigation, main landmark. */
export function AppShell({
  orgId,
  email,
  theme,
  children,
}: {
  orgId: string;
  email: string;
  /** The theme picked with the header toggle, if any (the toggle's first render). */
  theme?: Theme;
  children: ReactNode;
}) {
  const home = `/o/${orgId}`;
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-contrast"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-10 border-b border-border bg-surface/95 backdrop-blur-sm supports-[backdrop-filter]:bg-surface/85 print:hidden">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <div className="flex min-w-0 items-center gap-3 sm:gap-6">
            <Link href={home} className="flex shrink-0 items-center gap-2 font-semibold">
              <LogoMark size={28} />
              <span className="hidden sm:inline">InsightfulCell</span>
              <span className="sr-only sm:hidden">InsightfulCell</span>
            </Link>
            <nav aria-label="Main">
              <ul className="flex items-center gap-1">
                <li>
                  <NavLink href={home} sections={[`${home}/datasets`]}>
                    Home
                  </NavLink>
                </li>
                <li>
                  <NavLink href={`${home}/runs`} sections={[`${home}/runs/`]}>
                    Runs
                  </NavLink>
                </li>
                <li>
                  {/* The admin dashboard: its own page behind the admin key. */}
                  <NavLink href="/adminboard">Dashboard</NavLink>
                </li>
              </ul>
            </nav>
          </div>
          <UserMenu email={email} home={home} theme={theme} />
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 pt-8 pb-16 print:max-w-none print:p-0">
        {children}
      </main>
    </>
  );
}
