"use client";

import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";
import { ArrowLeft, LinkBreak } from "@/components/ui/icons";

const subscribeNothing = () => () => {};

/** True only when the visitor came from another page of this app, so "Go back" stays on it. */
function cameFromThisApp(): boolean {
  try {
    return document.referrer !== "" && new URL(document.referrer).origin === window.location.origin;
  } catch {
    return false;
  }
}

/** The link that led here, so a typo or a deleted item's id is easy to spot. */
export function RequestedPath() {
  const pathname = usePathname();
  if (!pathname || pathname === "/") return null;
  return (
    <div className="mt-5 flex items-start gap-3 rounded-control border border-dashed border-border bg-bg px-3 py-2.5 text-left">
      <LinkBreak aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-muted">Link you followed</p>
        <p className="mt-0.5 font-mono text-sm [overflow-wrap:anywhere]">{pathname}</p>
      </div>
    </div>
  );
}

/** Rendered only after hydration and only for visitors who arrived from inside the app. */
export function GoBackButton() {
  const canGoBack = useSyncExternalStore(subscribeNothing, cameFromThisApp, () => false);
  if (!canGoBack) return null;
  return (
    <button
      type="button"
      onClick={() => window.history.back()}
      className="press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
    >
      <ArrowLeft aria-hidden="true" className="size-4" />
      Go back
    </button>
  );
}
