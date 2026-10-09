import Link from "next/link";
import type { ReactNode } from "react";
import { NavLink } from "@/components/shell/nav-link";
import { UserMenu } from "@/components/shell/user-menu";

/** Global chrome: skip link, header landmark, navigation, main landmark. */
export function AppShell({
  orgId,
  email,
  children,
}: {
  orgId: string;
  email: string;
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
      <header className="sticky top-0 z-10 border-b border-border bg-surface/95 backdrop-blur-sm supports-[backdrop-filter]:bg-surface/85">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <div className="flex min-w-0 items-center gap-4 sm:gap-6">
            <Link href={home} className="flex shrink-0 items-center gap-2 font-semibold">
              <span aria-hidden="true" className="size-3.5 rounded-[3px] bg-accent" />
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
              </ul>
            </nav>
          </div>
          <UserMenu email={email} />
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 pt-8 pb-16">
        {children}
      </main>
    </>
  );
}
