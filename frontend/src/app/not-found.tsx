import type { Metadata } from "next";
import Link from "next/link";
import { LogoMark } from "@/components/ui/brand";
import { House } from "@/components/ui/icons";
import { GoBackButton, RequestedPath } from "@/components/ui/not-found-actions";

export const metadata: Metadata = { title: "Page not found" };

/** A small sheet of cells with one missing: the page that isn't there. Decorative only. */
const CELLS = 12;
const MISSING = 6;

function MissingCell() {
  return (
    <div aria-hidden="true" className="mx-auto grid w-fit grid-cols-4 gap-1.5">
      {Array.from({ length: CELLS }, (_, index) =>
        index === MISSING ? (
          <span
            key={index}
            className="grid h-7 w-11 place-items-center rounded-[4px] border-2 border-dashed border-accent bg-accent/10 font-mono text-xs font-semibold text-accent"
          >
            ?
          </span>
        ) : (
          <span key={index} className="h-7 w-11 rounded-[4px] border border-border bg-bg" />
        ),
      )}
    </div>
  );
}

export default function NotFound() {
  return (
    <main id="main" className="grid min-h-dvh place-items-center px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-6 flex flex-col items-center gap-2">
          <LogoMark size={112} className="drop-shadow-sm" />
          <p className="text-base font-semibold">InsightfulCell</p>
        </div>

        <div className="rounded-panel border border-border bg-surface px-6 py-8 text-center shadow-sm sm:px-8">
          <MissingCell />
          <p className="mt-6 font-mono text-sm font-medium tracking-wide text-accent">Error 404</p>
          <h1 className="mt-1 text-2xl font-semibold">Page not found</h1>
          <p className="mx-auto mt-2 max-w-[40ch] text-pretty text-muted">
            There’s nothing at this address. The link may be mistyped, or what it pointed to (a
            dataset, run or report) may have been deleted.
          </p>
          <RequestedPath />

          <div className="mt-6 flex flex-col-reverse justify-center gap-2 sm:flex-row">
            <GoBackButton />
            <Link
              href="/"
              className="press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border border-transparent bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110 pointer-coarse:min-h-11"
            >
              <House aria-hidden="true" className="size-4" />
              Go to your workspace
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
