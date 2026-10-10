import type { components } from "@/lib/api/schema";

/** Run types, straight from the generated API contract. */
export type Run = components["schemas"]["RunOut"];
export type RunSummary = components["schemas"]["RunSummaryOut"];
export type RunPage = components["schemas"]["RunPageOut"];
export type Stage = components["schemas"]["StageOut"];
export type RunStatus = components["schemas"]["RunStatus"];
export type StageStatus = components["schemas"]["StageStatus"];
export type RunEvent = components["schemas"]["RunEventOut"];
export type EventType = components["schemas"]["EventType"];
export type LatestRun = components["schemas"]["LatestRunOut"];

/** What the ingest stage reports (`StageOut.output` for `ingest`). Never cell values. */
export interface IngestOutput {
  format?: string;
  rows?: number;
  column_count?: number;
  columns?: { name: string; type: string; meaning?: string; personal_data?: string | null }[];
  personal_data_columns?: number;
  source_bytes?: number;
  parquet_bytes?: number;
  warnings?: string[];
}

/** A run-scoped ticket for one event stream, from `/api/runs/{id}/events-ticket`. */
export interface EventTicket {
  ticket: string;
  url: string;
  expires_at: string;
}

/** What the profile stage reports. */
export interface ProfileOutput {
  rows?: number;
  duplicate_rows?: number;
  complete_rows?: number;
  columns_with_issues?: number;
  personal_data_columns?: number;
}

/** What the understand stage reports. */
export interface UnderstandOutput {
  plan_source?: "model" | "cache" | "rules";
  provider?: string | null;
  model?: string | null;
  fallback_reason?: string | null;
  target?: string | null;
  analyses?: number;
  charts?: number;
  features?: number;
  rejected?: number;
  summary?: string;
}

/** What the clean_plan stage reports. */
export interface CleanPlanOutput {
  operations?: number;
  applied_automatically?: number;
  waiting_for_review?: number;
  not_applied?: number;
}
