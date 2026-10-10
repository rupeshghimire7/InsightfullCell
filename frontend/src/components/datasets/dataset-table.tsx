import Link from "next/link";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { formatBytes, formatCount } from "@/lib/format";
import type { components } from "@/lib/api/schema";
import { LocalTime } from "@/components/ui/local-time";

type Dataset = components["schemas"]["DatasetOut"];

export const FORMAT_LABEL: Record<string, string> = {
  csv: "CSV",
  tsv: "TSV",
  xlsx: "Excel",
  parquet: "Parquet",
  json: "JSON",
};

/** Datasets, newest first. The name opens the dataset; the last column shows its latest run. */
export function DatasetTable({ datasets, orgId }: { datasets: Dataset[]; orgId: string }) {
  return (
    <div
      role="region"
      aria-label="Datasets"
      tabIndex={0}
      className="overflow-x-auto rounded-panel border border-border bg-surface"
    >
      <table className="w-full border-collapse text-left text-[13px] leading-[1.35]">
        <caption className="sr-only">Datasets, newest first</caption>
        <thead>
          <tr className="border-b border-border text-muted">
            <th scope="col" className="px-4 py-2 font-medium">
              Name
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Format
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Size
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Versions
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Latest analysis
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Added
            </th>
          </tr>
        </thead>
        <tbody>
          {datasets.map((dataset) => (
            <tr key={dataset.id} className="border-b border-border last:border-b-0 hover:bg-bg/60">
              <th scope="row" className="max-w-[22rem] px-4 py-3 font-medium">
                <Link
                  href={`/o/${orgId}/datasets/${dataset.id}`}
                  className="block truncate text-accent underline-offset-2 hover:underline"
                  title={dataset.name}
                >
                  {dataset.name}
                </Link>
              </th>
              <td className="px-4 py-3">
                {dataset.latest_version
                  ? (FORMAT_LABEL[dataset.latest_version.format] ?? dataset.latest_version.format)
                  : "No file"}
              </td>
              <td className="tabular px-4 py-3 text-right whitespace-nowrap">
                {dataset.latest_version ? formatBytes(dataset.latest_version.bytes) : "None"}
              </td>
              <td className="tabular px-4 py-3 text-right">{formatCount(dataset.version_count)}</td>
              <td className="px-4 py-3">
                {dataset.latest_run ? (
                  <Link
                    href={`/o/${orgId}/runs/${dataset.latest_run.id}`}
                    className="inline-flex rounded-full"
                    title={`Open the latest analysis of ${dataset.name}`}
                  >
                    <RunStatusBadge status={dataset.latest_run.status} animated={false} />
                  </Link>
                ) : (
                  <span className="text-muted">Not analysed</span>
                )}
              </td>
              <td className="px-4 py-3 whitespace-nowrap text-muted">
                <LocalTime iso={dataset.created_at} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DatasetTableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div
      aria-hidden="true"
      className="rounded-panel border border-border bg-surface"
      data-testid="dataset-skeleton"
    >
      <div className="border-b border-border px-4 py-3">
        <div className="h-3 w-24 rounded bg-border" />
      </div>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="flex items-center gap-6 border-b border-border px-4 py-3 last:border-b-0"
        >
          <div className="h-3 w-1/3 animate-pulse rounded bg-border motion-reduce:animate-none" />
          <div className="h-3 w-12 animate-pulse rounded bg-border motion-reduce:animate-none" />
          <div className="h-5 w-20 animate-pulse rounded-full bg-border motion-reduce:animate-none" />
          <div className="ml-auto h-3 w-16 animate-pulse rounded bg-border motion-reduce:animate-none" />
        </div>
      ))}
    </div>
  );
}
