import { describe, expect, it } from "vitest";
import {
  runProgress,
  CANCELLABLE,
  formatDuration,
  isTerminal,
  RUN_STATUS,
  stageLabel,
  STAGE_STATUS,
} from "@/lib/runs/status";

describe("run status presentation", () => {
  it("labels every status with words", () => {
    expect(Object.keys(RUN_STATUS).sort()).toEqual([
      "awaiting_review",
      "cancelled",
      "expired",
      "failed",
      "queued",
      "running",
      "succeeded",
    ]);
    for (const { label } of Object.values(RUN_STATUS)) expect(label).toMatch(/^[A-Z]/);
    expect(Object.keys(STAGE_STATUS)).toHaveLength(4);
  });

  it("knows which statuses are final and which can be cancelled", () => {
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("awaiting_review")).toBe(false);
    expect([...CANCELLABLE].sort()).toEqual(["awaiting_review", "queued", "running"]);
  });
});

describe("stageLabel", () => {
  it("names known stages in plain language and humanises future ones", () => {
    expect(stageLabel("ingest").title).toBe("Verify and read the file");
    expect(stageLabel("clean_plan").title).toBe("Propose cleaning");
    expect(stageLabel("narrate_report")).toEqual({ title: "Narrate report", description: "" });
  });
});

describe("formatDuration", () => {
  const start = "2026-10-10T00:00:00Z";
  it.each([
    ["2026-10-10T00:00:04.4Z", "4 s"],
    ["2026-10-10T00:02:05Z", "2 min 05 s"],
    ["2026-10-10T01:03:00Z", "1 h 03 min"],
    ["2026-10-09T23:59:00Z", "0 s"],
  ])("%s -> %s", (end, expected) => {
    expect(formatDuration(start, end)).toBe(expected);
  });

  it("accepts a timestamp in milliseconds", () => {
    expect(formatDuration(start, Date.parse(start) + 61_000)).toBe("1 min 01 s");
  });
});

describe("runProgress", () => {
  it("says where a run is instead of an empty step", () => {
    expect(runProgress({ status: "succeeded", stage: null })).toBe("All steps done");
    expect(runProgress({ status: "queued", stage: null })).toBe("Waiting to start");
    expect(runProgress({ status: "running", stage: "ingest" })).toBe("Verify and read the file");
    expect(runProgress({ status: "running", stage: null })).toBe("Starting");
    expect(runProgress({ status: "awaiting_review", stage: null })).toBe("Waiting for your review");
    expect(runProgress({ status: "failed", stage: "ingest" })).toBe(
      "Stopped at: Verify and read the file",
    );
    expect(runProgress({ status: "cancelled", stage: null })).toBe("Stopped before the first step");
  });
});
