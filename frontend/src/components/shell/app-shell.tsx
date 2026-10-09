import Link from "next/link";
import type { ReactNode } from "react";
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
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-contrast"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-10 border-b border-border bg-surface">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4">
          <div className="flex items-center gap-6">
            <Link href={`/o/${orgId}`} className="font-semibold text-accent">
              InsightfulCell
            </Link>
            <nav aria-label="Main">
              <ul className="flex items-center gap-4">
                <li>
                  <Link
                    href={`/o/${orgId}`}
                    aria-current="page"
                    className="inline-flex min-h-6 items-center font-medium"
                  >
                    Home
                  </Link>
                </li>
              </ul>
            </nav>
          </div>
          <UserMenu email={email} />
        </div>
      </header>
      <main id="main" className="mx-auto max-w-5xl px-4 py-8">
        {children}
      </main>
    </>
  );
}
