import type { components } from "@/lib/api/schema";

/** Report types, straight from the generated API contract. */
export type Report = components["schemas"]["ReportOut"];
export type ReportSection = components["schemas"]["ReportSectionOut"];
export type ReportFact = components["schemas"]["FactOut"];
export type Sentence = components["schemas"]["SentenceOut"];
export type Chart = components["schemas"]["ChartOut"];
export type ChartPoint = components["schemas"]["ChartPointOut"];
export type ReportMap = components["schemas"]["MapOut"];
export type MapRegion = components["schemas"]["MapRegionOut"];
export type Scope = components["schemas"]["ScopeOut"];
export type Dashboard = components["schemas"]["DashboardOut"];
export type Tile = components["schemas"]["TileOut"];
export type Insight = components["schemas"]["InsightOut"];
export type SuggestedQuestion = components["schemas"]["SuggestedQuestionOut"];
