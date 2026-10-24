"use client";

import { useEffect } from "react";

/**
 * A one-time note on the sign-in page after something finished elsewhere ("Delete my account",
 * a password reset). The flag comes in the address (`?account=deleted`, `?reset=done`); it is
 * removed from it at once, so a reload, a bookmark or the register link never shows it again.
 */
export function SignInNotice({ param, children }: { param: string; children: string }) {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(param)) return;
    url.searchParams.delete(param);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
  }, [param]);
  return (
    <p
      role="status"
      className="mb-6 rounded-control border border-accent/40 bg-accent/10 px-3 py-2 text-sm"
    >
      {children}
    </p>
  );
}
