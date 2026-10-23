import type { Run, RunEvent, Stage } from "@/lib/runs/types";

/**
 * Applies one live event to the run the page shows. Pure: the page keeps the result, and the
 * authoritative snapshot (`GET /v1/runs/{id}`) replaces it whenever it is re-read. Events at or
 * below `last_event_seq` were already applied (a replay after reconnecting) and are ignored.
 */
export interface RunView {
  run: Run;
  /** A short, user-facing note about the current situation (waiting, retrying, reminder). */
  note: string | null;
}

export function initialView(run: Run): RunView {
  return { run, note: null };
}

export function applyEvent(view: RunView, event: RunEvent): RunView {
  if (event.seq <= view.run.last_event_seq) return view;
  const run: Run = { ...view.run, last_event_seq: event.seq, updated_at: event.at };
  let note = view.note;
  const data = event.data as Record<string, unknown>;

  switch (event.type) {
    case "run.queued":
      run.status = "queued";
      break;
    case "run.waiting":
      note = "Waiting for a free slot: your workspace is running its maximum number of analyses.";
      break;
    case "run.started":
      run.status = "running";
      run.started_at ??= event.at;
      note = null;
      break;
    case "stage.started":
      run.status = "running";
      run.stage = event.stage;
      note = null;
      run.stages = patchStage(run.stages, event.stage, {
        status: "running",
        started_at: event.at,
        finished_at: null,
        reused: false,
      });
      break;
    case "stage.completed":
      run.stages = patchStage(run.stages, event.stage, {
        status: "succeeded",
        finished_at: event.at,
      });
      break;
    case "stage.reused":
      run.stages = patchStage(run.stages, event.stage, { status: "succeeded", reused: true });
      break;
    case "stage.failed":
      run.stages = patchStage(run.stages, event.stage, { status: "failed", finished_at: event.at });
      break;
    case "stage.retrying": {
      run.stages = patchStage(run.stages, event.stage, { status: "pending", finished_at: null });
      const attempt = typeof data.attempt === "number" ? data.attempt : null;
      const max = typeof data.max_attempts === "number" ? data.max_attempts : null;
      note =
        data.reason === "worker_stopped" || data.reason === "out_of_memory"
          ? "The worker stopped unexpectedly. The step will run again."
          : attempt && max
            ? `Something went wrong; trying again (attempt ${attempt + 1} of ${max}).`
            : "Something went wrong; trying again.";
      break;
    }
    case "run.moved_to_large_queue":
      run.queue = "large";
      note = "This file needs more memory. It moved to a larger worker.";
      break;
    case "phase.completed":
      if (data.phase === "A") {
        run.phase = "B";
        run.stage = null;
      }
      break;
    case "review.required":
      run.status = "awaiting_review";
      run.review_requested_at = event.at;
      run.stage = null;
      break;
    case "review.reminder":
      note = "This run is still waiting for your review. It expires if no one decides.";
      break;
    case "review.approved":
      run.status = "queued";
      run.phase = "B";
      note = null;
      break;
    case "review.revision_requested":
      run.status = "queued";
      run.phase = "A";
      run.stage = null;
      note = "Planning again with your changes. The plan comes back to you for review.";
      break;
    case "run.succeeded":
      run.status = "succeeded";
      run.finished_at = event.at;
      run.stage = null;
      note = null;
      break;
    case "run.failed":
      run.status = "failed";
      run.finished_at = event.at;
      run.failure = {
        code: typeof data.code === "string" ? data.code : "unknown",
        detail: typeof data.detail === "string" ? data.detail : null,
        hint: typeof data.hint === "string" ? data.hint : null,
      };
      note = null;
      break;
    case "run.cancelled":
      run.status = "cancelled";
      run.finished_at = event.at;
      note = null;
      break;
    case "run.expired":
      run.status = "expired";
      run.finished_at = event.at;
      note = null;
      break;
    case "run.retried":
      run.status = "queued";
      run.failure = null;
      run.finished_at = null;
      run.stages = run.stages.map((stage) =>
        stage.status === "failed" || stage.status === "running"
          ? { ...stage, status: "pending", started_at: null, finished_at: null }
          : stage,
      );
      note = null;
      break;
  }
  return { run, note };
}

function patchStage(stages: Stage[], name: string | null, patch: Partial<Stage>): Stage[] {
  if (!name) return stages;
  return stages.map((stage) => (stage.name === name ? { ...stage, ...patch } : stage));
}

/** Events after which the page re-reads the run: they change what only the server knows in full
 * (a stage's whole output, the run's ETag). */
export const REFRESH_AFTER: ReadonlySet<RunEvent["type"]> = new Set([
  "stage.completed",
  "review.required",
  "review.approved",
  "review.revision_requested",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.expired",
  "run.retried",
  "run.moved_to_large_queue",
]);

/** Events after which the server closes the stream. */
export const FINAL_EVENTS: ReadonlySet<RunEvent["type"]> = new Set([
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.expired",
]);
