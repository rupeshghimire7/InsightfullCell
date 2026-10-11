"use client";

import { useEffect } from "react";
import { Printer } from "@/components/ui/icons";

/**
 * Print the page; the browser's print dialog also saves it as a PDF. However printing starts
 * (this button or the browser's own command), collapsed sections ("Show the numbers") are opened
 * for the printout and closed again afterwards.
 */
export function PrintButton() {
  useEffect(() => {
    let opened: HTMLDetailsElement[] = [];
    const before = () => {
      opened = [...document.querySelectorAll<HTMLDetailsElement>("main details:not([open])")];
      for (const details of opened) details.open = true;
    };
    const after = () => {
      for (const details of opened) details.open = false;
      opened = [];
    };
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  return (
    <button
      type="button"
      onClick={() => window.print()}
      className="press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
    >
      <Printer aria-hidden="true" className="size-4" />
      Print or save as PDF
    </button>
  );
}
