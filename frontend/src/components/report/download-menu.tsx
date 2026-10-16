"use client";

import { useEffect, useRef } from "react";
import { CaretDown, DownloadSimple } from "@/components/ui/icons";

/** The report's downloadable formats, all built on the server from the same stored report. */
export const REPORT_FORMATS = [
  { ext: "pdf", label: "PDF", detail: "A4 pages, ready to share or print" },
  { ext: "docx", label: "Word", detail: "Editable document (.docx)" },
  { ext: "md", label: "Markdown", detail: "Plain text with tables (.md)" },
  { ext: "html", label: "HTML", detail: "One self-contained web page" },
] as const;

/**
 * "Download" with a short list of formats. A native disclosure (keyboard and screen readers
 * work as they do everywhere); it closes after a choice, on Escape and on a click elsewhere.
 */
export function DownloadMenu({ runId }: { runId: string }) {
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

  return (
    <details ref={menu} className="group relative">
      <summary className="press inline-flex min-h-9 cursor-pointer list-none items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11 [&::-webkit-details-marker]:hidden">
        <DownloadSimple aria-hidden="true" className="size-4" />
        Download
        <CaretDown
          aria-hidden="true"
          className="size-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <ul
        aria-label="Download the report as"
        className="absolute left-0 z-30 mt-1 w-64 rounded-panel border border-border bg-surface p-1 shadow-lg sm:right-0 sm:left-auto"
      >
        {REPORT_FORMATS.map((format) => (
          <li key={format.ext}>
            <a
              href={`/api/backend/v1/runs/${runId}/report.${format.ext}`}
              download
              onClick={() => {
                if (menu.current) menu.current.open = false;
              }}
              className="flex min-h-11 flex-col justify-center rounded-control px-3 py-1.5 hover:bg-bg focus-visible:bg-bg"
            >
              <span className="text-sm font-medium">{format.label}</span>
              <span className="text-xs text-muted">{format.detail}</span>
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
