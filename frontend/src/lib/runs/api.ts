import { ProblemError, readProblem } from "@/lib/problem";
import type { Deleted, EventTicket, ReviewMode, Run } from "@/lib/runs/types";

/** Browser calls for runs. Everything but the event stream goes through the web app's BFF. */
async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new ProblemError(await readProblem(response));
  return (await response.json()) as T;
}

export const runsApi = {
  start: (input: {
    datasetId: string;
    versionId?: string;
    idempotencyKey: string;
    reviewMode?: ReviewMode;
  }) =>
    call<Run>("/api/backend/v1/runs", {
      method: "POST",
      headers: { "Idempotency-Key": input.idempotencyKey },
      body: JSON.stringify({
        dataset_id: input.datasetId,
        ...(input.versionId ? { dataset_version_id: input.versionId } : {}),
        review_mode: input.reviewMode ?? "auto",
      }),
    }),
  get: (runId: string, signal?: AbortSignal) =>
    call<Run>(`/api/backend/v1/runs/${encodeURIComponent(runId)}`, { signal }),
  cancel: (runId: string, rowVersion: number) =>
    call<Run>(`/api/backend/v1/runs/${encodeURIComponent(runId)}:cancel`, {
      method: "POST",
      headers: { "If-Match": `"${rowVersion}"` },
    }),
  retry: (runId: string, rowVersion: number) =>
    call<Run>(`/api/backend/v1/runs/${encodeURIComponent(runId)}:retry`, {
      method: "POST",
      headers: { "If-Match": `"${rowVersion}"` },
    }),
  /** Delete a run and its explorations (refused with 409 `still-running` while it runs). */
  remove: (runId: string) =>
    call<Deleted>(`/api/backend/v1/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
  /** Delete a dataset with its versions and every analysis of it. */
  removeDataset: (datasetId: string) =>
    call<Deleted>(`/api/backend/v1/datasets/${encodeURIComponent(datasetId)}`, {
      method: "DELETE",
    }),
  /** Write a simplified report's words again (after a model outage); 202 with the run. */
  renarrate: (runId: string, rowVersion: number) =>
    call<Run>(`/api/backend/v1/runs/${encodeURIComponent(runId)}/report:renarrate`, {
      method: "POST",
      headers: { "If-Match": `"${rowVersion}"` },
    }),
  /** Explore a subset of a finished analysis: 202 with the new (exploratory) run. */
  drilldown: (runId: string, input: { column: string; values: string[]; idempotencyKey: string }) =>
    call<Run>(`/api/backend/v1/runs/${encodeURIComponent(runId)}/drilldowns`, {
      method: "POST",
      headers: { "Idempotency-Key": input.idempotencyKey },
      body: JSON.stringify({ column: input.column, values: input.values }),
    }),
  ticket: (runId: string) =>
    call<EventTicket>(`/api/runs/${encodeURIComponent(runId)}/events-ticket`, { method: "POST" }),
};

export type RunsApi = typeof runsApi;
