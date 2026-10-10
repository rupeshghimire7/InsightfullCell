import { ProblemError, readProblem } from "@/lib/problem";
import type { DecideRequest } from "@/lib/plans/types";
import type { Run } from "@/lib/runs/types";

/** Browser calls for plans, through the web app's BFF. */
export const plansApi = {
  decide: async (runId: string, rowVersion: number, body: DecideRequest): Promise<Run> => {
    const response = await fetch(
      `/api/backend/v1/runs/${encodeURIComponent(runId)}/cleaning-plan:decide`,
      {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "If-Match": `"${rowVersion}"`,
        },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) throw new ProblemError(await readProblem(response));
    return (await response.json()) as Run;
  },
  recipeUrl: (runId: string) => `/api/backend/v1/runs/${encodeURIComponent(runId)}/recipe`,
};

export type PlansApi = typeof plansApi;
