import { describe, expect, it } from "vitest";
import { applyEvent, FINAL_EVENTS, initialView, REFRESH_AFTER } from "@/lib/runs/reducer";
import { event, run, stage } from "./runs-fixtures";

function play(...events: Parameters<typeof applyEvent>[1][]) {
  return events.reduce(applyEvent, initialView(run()));
}

describe("applyEvent", () => {
  it("walks a run from queued to succeeded", () => {
    const view = play(
      event(2, "run.started"),
      event(3, "stage.started", { stage: "ingest" }),
      event(4, "stage.completed", { stage: "ingest" }),
      event(5, "phase.completed", { data: { phase: "A" } }),
      event(6, "phase.completed", { data: { phase: "B" } }),
      event(7, "run.succeeded"),
    );
    expect(view.run.status).toBe("succeeded");
    expect(view.run.phase).toBe("B");
    expect(view.run.started_at).toBe("2026-10-10T01:00:02Z");
    expect(view.run.finished_at).toBe("2026-10-10T01:00:07Z");
    expect(view.run.stages[0]).toMatchObject({
      status: "succeeded",
      finished_at: event(4, "x" as never).at,
    });
    expect(view.run.last_event_seq).toBe(7);
    expect(view.note).toBeNull();
  });

  it("ignores events it has already applied (a replay after reconnecting)", () => {
    const once = play(event(2, "run.started"), event(3, "stage.started", { stage: "ingest" }));
    expect(applyEvent(once, event(3, "stage.failed", { stage: "ingest" }))).toBe(once);
    expect(applyEvent(once, event(1, "run.queued"))).toBe(once);
  });

  it("records a failure in the user's words", () => {
    const view = play(
      event(2, "run.started"),
      event(3, "stage.failed", { stage: "ingest" }),
      event(4, "run.failed", {
        data: { code: "parse-failure", detail: "Could not read.", hint: "Check it." },
      }),
    );
    expect(view.run.status).toBe("failed");
    expect(view.run.failure).toEqual({
      code: "parse-failure",
      detail: "Could not read.",
      hint: "Check it.",
    });
    expect(view.run.stages[0]?.status).toBe("failed");
  });

  it("tolerates a failure event without details", () => {
    const view = play(event(2, "run.failed"));
    expect(view.run.failure).toEqual({ code: "unknown", detail: null, hint: null });
  });

  it.each([
    [event(2, "run.waiting"), /free slot/],
    [
      event(2, "stage.retrying", { stage: "ingest", data: { attempt: 1, max_attempts: 3 } }),
      /attempt 2 of 3/,
    ],
    [
      event(2, "stage.retrying", { stage: "ingest", data: { reason: "worker_stopped" } }),
      /stopped unexpectedly/,
    ],
    [event(2, "stage.retrying", { stage: "ingest" }), /^Something went wrong; trying again\.$/],
    [event(2, "run.moved_to_large_queue"), /more memory/],
    [event(2, "review.reminder"), /still waiting/],
  ])("explains %j in a note", (incoming, note) => {
    expect(play(incoming).note).toMatch(note);
  });

  it("clears the note once work resumes", () => {
    expect(play(event(2, "run.waiting"), event(3, "run.started")).note).toBeNull();
  });

  it("moves through the review gate", () => {
    const waiting = play(event(2, "run.started"), event(3, "review.required"));
    expect(waiting.run.status).toBe("awaiting_review");
    expect(waiting.run.review_requested_at).toBe("2026-10-10T01:00:03Z");
    const approved = applyEvent(waiting, event(4, "review.approved"));
    expect(approved.run).toMatchObject({ status: "queued", phase: "B" });
  });

  it("handles cancel, expiry and a retry that clears the failure", () => {
    expect(play(event(2, "run.cancelled")).run.status).toBe("cancelled");
    expect(play(event(2, "run.expired")).run.status).toBe("expired");
    const failed = initialView(
      run({
        status: "failed",
        finished_at: "x",
        failure: { code: "c", detail: null, hint: null },
        stages: [stage({ status: "failed" }), stage({ name: "two", status: "succeeded" })],
      }),
    );
    const retried = applyEvent(failed, event(2, "run.retried"));
    expect(retried.run).toMatchObject({ status: "queued", failure: null, finished_at: null });
    expect(retried.run.stages.map((s) => s.status)).toEqual(["pending", "succeeded"]);
  });

  it("marks a reused stage", () => {
    const view = play(event(2, "stage.reused", { stage: "ingest" }));
    expect(view.run.stages[0]).toMatchObject({ status: "succeeded", reused: true });
  });

  it("leaves stages alone for events without a stage, and unknown stages untouched", () => {
    const view = play(
      event(2, "stage.started", { stage: null }),
      event(3, "stage.started", { stage: "other" }),
    );
    expect(view.run.stages[0]?.status).toBe("pending");
  });
});

describe("event sets", () => {
  it("refreshes after events that change server-only data and ends on final events", () => {
    expect(REFRESH_AFTER.has("stage.completed")).toBe(true);
    expect(REFRESH_AFTER.has("stage.started")).toBe(false);
    expect([...FINAL_EVENTS].sort()).toEqual([
      "run.cancelled",
      "run.expired",
      "run.failed",
      "run.succeeded",
    ]);
  });
});
