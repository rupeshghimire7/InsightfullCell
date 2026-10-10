import type { RunStatus, StageStatus } from "@/lib/runs/types";

export const TERMINAL: ReadonlySet<RunStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);
export const CANCELLABLE: ReadonlySet<RunStatus> = new Set([
  "queued",
  "running",
  "awaiting_review",
]);

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL.has(status);
}

export type Tone = "neutral" | "active" | "attention" | "positive" | "negative";

/** How a status reads and looks. Colour is never the only signal: every badge has words. */
export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "active" },
  awaiting_review: { label: "Needs review", tone: "attention" },
  succeeded: { label: "Succeeded", tone: "positive" },
  failed: { label: "Failed", tone: "negative" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  expired: { label: "Expired", tone: "neutral" },
};

export const STAGE_STATUS: Record<StageStatus, string> = {
  pending: "Waiting",
  running: "In progress",
  succeeded: "Done",
  failed: "Failed",
};

/** Plain-language names for stages; unknown (future) stages fall back to their id. */
const STAGE_LABEL: Record<string, { title: string; description: string }> = {
  ingest: {
    title: "Verify and read the file",
    description:
      "Checks the stored bytes match what you uploaded, reads every row into typed columns, and finds personal data to mask.",
  },
  profile: {
    title: "Profile every column",
    description:
      "Counts missing values, duplicates, outliers and placeholder codes, column by column.",
  },
  understand: {
    title: "Plan the analysis",
    description:
      "Our engine plans first; a language model, if one is set up, refines the plan from a description of the table without any rows. Every suggestion is checked.",
  },
  clean_plan: {
    title: "Propose cleaning",
    description:
      "Lists each change with the rows it affects, then waits for you to check the plan.",
  },
  clean_apply: {
    title: "Apply the cleaning",
    description:
      "Applies the changes you approved to a copy of your data. The upload is never changed.",
  },
  geo: {
    title: "Find the places",
    description: "Matches place names, postcodes and coordinates to standard countries and states.",
  },
  features: {
    title: "Test the extra measures",
    description:
      "Keeps a derived measure, such as the weekday of a date, only if it tells you more about the outcome than chance does.",
  },
  mine: {
    title: "Answer the questions",
    description:
      "Tests every question in the plan and keeps the share of false findings across the whole analysis at 5%.",
  },
  sensitivity: {
    title: "Check the findings hold",
    description:
      "Re-tests the strongest findings without the cleaning that removed or merged data.",
  },
  rank: {
    title: "Rank the findings",
    description: "Puts the strongest and most relevant findings first and drops repeats.",
  },
  report: {
    title: "Write the report",
    description:
      "Writes each answer from the facts and checks every number and source before it is kept.",
  },
};

export function stageLabel(name: string): { title: string; description: string } {
  return (
    STAGE_LABEL[name] ?? {
      title: name.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase()),
      description: "",
    }
  );
}

/** Whole seconds between two instants, as "4 s", "2 min 05 s" or "1 h 03 min". */
export function formatDuration(fromIso: string, toIso: string | number): string {
  const end = typeof toIso === "number" ? toIso : Date.parse(toIso);
  const seconds = Math.max(0, Math.round((end - Date.parse(fromIso)) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}
