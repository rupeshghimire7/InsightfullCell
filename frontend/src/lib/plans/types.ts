import type { components } from "@/lib/api/schema";

/** Plan types, straight from the generated API contract. */
export type Plan = components["schemas"]["PlanOut"];
export type PlanColumn = components["schemas"]["PlanColumnOut"];
export type CleaningPlan = components["schemas"]["CleaningPlanOut"];
export type CleaningOperation = components["schemas"]["CleaningOperationOut"];
export type DictionaryEntry = components["schemas"]["DictionaryEntryOut"];
export type PlanFeature = components["schemas"]["FeatureOut"];
export type PlanAnalysis = components["schemas"]["AnalysisItemOut"];
export type PlanChart = components["schemas"]["ChartItemOut"];
export type DecideRequest = components["schemas"]["DecideCleaningRequest"];

/** The profile stored for a column (`PlanColumnOut.profile`): counts for every column; the
 * rest only for columns without personal data. Values are the person's own data, shown back
 * to them; none of it is ever sent to a model. */
export interface ColumnProfile {
  rows?: number;
  missing?: number;
  missing_share?: number;
  distinct?: number;
  unparsed?: number;
  numeric?: {
    count: number;
    p05: number | null;
    p50: number | null;
    p95: number | null;
    outliers: number;
    sentinels?: number[];
    histogram?: number[];
    histogram_range?: [number, number] | null;
  } | null;
  categorical?: {
    top?: { value: string; count: number }[];
    other_count?: number;
    case_variant_values?: number;
  } | null;
  datetime?: {
    count: number;
    p05: string | null;
    p95: string | null;
    granularity: string;
    span_days: number | null;
  } | null;
  text?: { length_p50: number | null; words_p50: number | null } | null;
  reasons?: string[];
}
