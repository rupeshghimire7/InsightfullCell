import "server-only";
import { serverApi } from "@/lib/backend";
import type { RunSummary } from "@/lib/runs/types";

/**
 * What a report or dashboard page needs besides the document: whether the person may explore
 * groups (analysts and above) and the explorations already started from this run. Both are
 * extras: if either call fails the page still renders, without them.
 */
export async function reportContext(
  runId: string,
  isExploration: boolean,
): Promise<{ canExplore: boolean; explorations: RunSummary[] }> {
  const api = serverApi();
  const [me, children] = await Promise.all([
    api.GET("/v1/me").catch(() => null),
    isExploration
      ? Promise.resolve(null)
      : api
          .GET("/v1/runs", { params: { query: { parent_run_id: runId, limit: 25 } } })
          .catch(() => null),
  ]);
  const role = me?.data?.role;
  return {
    canExplore: !isExploration && role !== undefined && role !== "viewer",
    explorations: children?.data?.items ?? [],
  };
}
