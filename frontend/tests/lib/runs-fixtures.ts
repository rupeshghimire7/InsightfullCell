import type { Run, RunEvent, Stage } from "@/lib/runs/types";

export function stage(overrides: Partial<Stage> = {}): Stage {
  return {
    name: "ingest",
    phase: "A",
    position: 0,
    status: "pending",
    reused: false,
    attempts: 0,
    started_at: null,
    finished_at: null,
    output: {},
    ...overrides,
  };
}

export function run(overrides: Partial<Run> = {}): Run {
  return {
    id: "01a120e9-cc81-7ec1-9428-29aea00bb693",
    dataset_id: "01a120e9-0000-7000-8000-000000000001",
    dataset_version_id: "01a120e9-0000-7000-8000-000000000002",
    dataset_name: "Home sales 2026",
    status: "queued",
    phase: "A",
    stage: null,
    queue: "standard",
    attempts: 0,
    row_version: 1,
    last_event_seq: 1,
    failure: null,
    created_at: "2026-10-10T01:00:00Z",
    updated_at: "2026-10-10T01:00:00Z",
    started_at: null,
    finished_at: null,
    review_requested_at: null,
    review_mode: "manual",
    stages: [stage()],
    events_url: "/v1/runs/01a120e9-cc81-7ec1-9428-29aea00bb693/events",
    waiting_for_worker: false,
    ...overrides,
  };
}

export function event(
  seq: number,
  type: RunEvent["type"],
  extra: Partial<RunEvent> = {},
): RunEvent {
  return {
    seq,
    type,
    stage: null,
    data: {},
    at: `2026-10-10T01:00:${String(seq).padStart(2, "0")}Z`,
    ...extra,
  };
}

export const INGEST_OUTPUT = {
  format: "csv",
  rows: 1234,
  column_count: 3,
  columns: [
    { name: "id", type: "BIGINT" },
    { name: "suburb", type: "VARCHAR" },
    { name: "price", type: "DOUBLE" },
  ],
  source_bytes: 48_000,
  parquet_bytes: 12_000,
  warnings: [],
};
