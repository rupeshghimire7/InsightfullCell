import type { Chart, Report } from "@/lib/report/types";
import { run } from "./runs-fixtures";

export function chart(overrides: Partial<Chart> = {}): Chart {
  return {
    kind: "bar",
    title: "Complaint rate by state",
    x_label: "State",
    y_label: "Complaint rate",
    unit: null,
    value_format: "percent",
    note: "Only states with at least 20 rows.",
    points: [
      { label: "NSW", value: 0.31, n: 120, x: null, x2: null },
      { label: "VIC", value: 0.12, n: 80, x: null, x2: null },
    ],
    ...overrides,
  };
}

export function report(overrides: Partial<Report> = {}): Report {
  return {
    run: run({ status: "succeeded" }),
    title: "Analysis for jobs.csv",
    dataset_name: "jobs.csv",
    created_at: "2026-10-10T00:00:00Z",
    revision: 1,
    summary: [{ text: "Complaints are most common in NSW.", citations: ["F1"] }],
    sections: [
      {
        analysis_id: "segment:State,Complaint",
        kind: "segment",
        kind_label: "Comparison",
        question: "Do complaints differ by state?",
        requested_by_person: true,
        answer: [{ text: "NSW has the highest rate, 31%.", citations: ["F1"] }],
        chart: chart(),
        columns: [{ name: "State", display_name: "State" }],
        facts: ["F1"],
        skipped: null,
        written_by: "engine",
      },
      {
        analysis_id: "distribution:Hours",
        kind: "distribution",
        kind_label: "Distribution",
        question: "How are hours distributed?",
        requested_by_person: false,
        answer: [],
        chart: null,
        columns: [],
        facts: [],
        skipped: "Too few rows.",
        written_by: null,
      },
    ],
    facts: [
      {
        id: "F1",
        analysis_id: "segment:State,Complaint",
        kind: "segment",
        statement: "NSW complaint rate is 31%.",
        columns: [{ name: "State", display_name: "State" }],
        rows: 1200,
        method: "Share of rows by group",
        values: {},
        tier: "strong",
        test: {
          method: "Kruskal-Wallis test",
          effect_name: "eta",
          effect: 0.35,
          ci_low: 0.32,
          ci_high: 0.38,
          p_value: 1e-9,
          q_value: 2e-9,
        },
        check: {
          outcome: "flips",
          note: "On minimally cleaned data its direction changes.",
          effect: -0.1,
          p_value: 0.2,
        },
        content_hash: "a".repeat(64),
      },
    ],
    method_notes: ["Rates are shares of rows."],
    highlights: ["F1"],
    evidence: { tests: 12, reliable: 3, alpha: 0.05, rechecked: 3 },
    features: [
      { label: "Weekday of Job date", outcome: "kept", redundant_with: null },
      { label: "Log of Hours", outcome: "redundant", redundant_with: "Hours" },
    ],
    removed_questions: 1,
    requests: [{ request: "Look at state", status: "added", note: "Added.", source: "rules" }],
    narration: {
      source: "engine",
      provider: null,
      model: null,
      fallback_reason: "disabled",
      sentences_kept: 2,
      sentences_dropped: 0,
    },
    preparation: {
      applied: [{ label: "Trim spaces", column: "State", rows: 2 }],
      not_applied: 0,
      reviewed_by_person: true,
      rows_before: 1250,
      rows_after: 1200,
    },
    understanding: {
      columns: 8,
      domain: "field service",
      grain: "A job.",
      masked_columns: 1,
      planned_by: "rules",
      rows: 1200,
      summary: "One row per job.",
      target: "Complaint",
    },
    ...overrides,
  } as Report;
}
