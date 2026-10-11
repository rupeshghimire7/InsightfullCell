import Link from "next/link";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { LocalTime } from "@/components/ui/local-time";
import type { RunSummary } from "@/lib/runs/types";

/** The explorations (drill-downs) already started from this analysis. */
export function Explorations({ orgId, runs }: { orgId: string; runs: readonly RunSummary[] }) {
  if (runs.length === 0) return null;
  return (
    <section aria-labelledby="explorations-title" className="space-y-3">
      <h2 id="explorations-title" className="text-lg font-semibold">
        Explorations of this analysis
      </h2>
      <ul className="divide-y divide-border rounded-panel border border-border bg-surface">
        {runs.map((run) => {
          const scope = run.drilldown;
          const href =
            run.status === "succeeded"
              ? `/o/${orgId}/runs/${run.id}/dashboard`
              : `/o/${orgId}/runs/${run.id}`;
          return (
            <li key={run.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <Link
                href={href}
                className="min-w-0 flex-1 font-medium underline-offset-2 hover:underline"
              >
                {scope ? `${scope.column}: ${scope.values.join(", ")}` : "Exploration"}
              </Link>
              <span className="text-xs text-muted">
                <LocalTime iso={run.created_at} />
              </span>
              <RunStatusBadge status={run.status} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
