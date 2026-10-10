import type { components } from "@/lib/api/schema";

/** Report types, straight from the generated API contract. */
export type Report = components["schemas"]["ReportOut"];
export type ReportSection = components["schemas"]["ReportSectionOut"];
export type ReportFact = components["schemas"]["FactOut"];
export type Sentence = components["schemas"]["SentenceOut"];
export type Chart = components["schemas"]["ChartOut"];
export type ChartPoint = components["schemas"]["ChartPointOut"];
