"use client";

import { useEffect } from "react";

/**
 * "Your account … was deleted", shown once after "Delete my account". The flag comes in the
 * address (`?account=deleted`); it is removed from it at once, so a reload, a bookmark or the
 * register link never shows the notice again.
 */
export function AccountDeletedNotice() {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("account")) return;
    url.searchParams.delete("account");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
  }, []);
  return (
    <p
      role="status"
      className="mb-6 rounded-control border border-accent/40 bg-accent/10 px-3 py-2 text-sm"
    >
      Your account and everything in it were deleted.
    </p>
  );
}
