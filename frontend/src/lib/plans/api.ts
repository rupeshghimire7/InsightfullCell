import { ProblemError, readProblem } from "@/lib/problem";
import type { DecideRequest, ReviseRequest } from "@/lib/plans/types";
import type { Run } from "@/lib/runs/types";

async function post<T>(path: string, rowVersion: number, body: T): Promise<Run> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "If-Match": `"${rowVersion}"`,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new ProblemError(await readProblem(response));
  return (await response.json()) as Run;
}

/** Browser calls for plans, through the web app's BFF. */
export const plansApi = {
  /** Approve the plan: the analysis continues with the cleaning and the questions as they are. */
  decide: (runId: string, rowVersion: number, body: DecideRequest): Promise<Run> =>
    post(
      `/api/backend/v1/runs/${encodeURIComponent(runId)}/cleaning-plan:decide`,
      rowVersion,
      body,
    ),
  /** Plan again with the person's changes; the run comes back for review. */
  revise: (runId: string, rowVersion: number, body: ReviseRequest): Promise<Run> =>
    post(`/api/backend/v1/runs/${encodeURIComponent(runId)}/plan:revise`, rowVersion, body),
  recipeUrl: (runId: string) => `/api/backend/v1/runs/${encodeURIComponent(runId)}/recipe`,
};

export type PlansApi = typeof plansApi;
