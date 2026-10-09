import { DatasetTableSkeleton } from "@/components/datasets/dataset-table";

export default function Loading() {
  return (
    <div className="space-y-10" role="status" aria-label="Loading">
      <div className="h-8 w-24 rounded bg-border" aria-hidden="true" />
      <div
        className="h-40 rounded-panel border border-dashed border-border bg-surface"
        aria-hidden="true"
      />
      <DatasetTableSkeleton />
    </div>
  );
}
