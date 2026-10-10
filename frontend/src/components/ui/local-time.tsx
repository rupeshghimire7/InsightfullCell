"use client";

import { useSyncExternalStore } from "react";
import { formatDate, formatUtc } from "@/lib/format";

const noSubscription = () => () => {};

/**
 * A moment in the viewer's own time zone and locale. The server cannot know either, so the
 * server render (and the hydration pass) shows a locale-independent UTC time, and the browser
 * then switches to local time: no hydration mismatch, and never the server's clock.
 */
export function LocalTime({ iso }: { iso: string }) {
  const inBrowser = useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
  return <time dateTime={iso}>{inBrowser ? formatDate(iso) : formatUtc(iso)}</time>;
}
