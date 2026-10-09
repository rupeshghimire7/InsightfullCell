import Link from "next/link";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { formatDate } from "@/lib/format";
import { formatDuration, stageLabel } from "@/lib/runs/status";
import type { RunSummary } from "@/lib/runs/types";

/** Runs, newest first. Each row opens the run. */
export function RunTable({
  runs,
  orgId,
  showDataset = true,
}: {
  runs: RunSummary[];
  orgId: string;
  showDataset?: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-panel border border-border bg-surface">
      <table className="w-full border-collapse text-left text-[13px] leading-[1.35]">
        <caption className="sr-only">Analysis runs, newest first</caption>
        <thead>
          <tr className="border-b border-border text-muted">
            <th scope="col" className="px-4 py-2 font-medium">
              {showDataset ? "Dataset" : "Run"}
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Status
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Step
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Started
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Took
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-b border-border last:border-b-0 hover:bg-bg/60">
              <th scope="row" className="max-w-[22rem] px-4 py-3 font-medium">
                <Link
                  href={`/o/${orgId}/runs/${run.id}`}
                  className="block truncate text-accent underline-offset-2 hover:underline"
                  title={showDataset ? run.dataset_name : run.id}
                >
                  {showDataset ? run.dataset_name || "Dataset" : `Run ${run.id.slice(-8)}`}
                </Link>
              </th>
              <td className="px-4 py-3">
                <RunStatusBadge status={run.status} />
              </td>
              <td className="px-4 py-3 text-muted">
                {run.stage ? stageLabel(run.stage).title : run.failure ? run.failure.code : "–"}
              </td>
              <td className="px-4 py-3 whitespace-nowrap text-muted">
                <time dateTime={run.created_at}>{formatDate(run.created_at)}</time>
              </td>
              <td className="tabular px-4 py-3 text-right whitespace-nowrap">
                {run.started_at && run.finished_at
                  ? formatDuration(run.started_at, run.finished_at)
                  : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
