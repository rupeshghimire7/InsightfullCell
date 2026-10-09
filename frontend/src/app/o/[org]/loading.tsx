import { DatasetTableSkeleton } from "@/components/datasets/dataset-table";

export default function Loading() {
  return (
    <div className="space-y-10" role="status" aria-label="Loading">
      <div className="space-y-2" aria-hidden="true">
        <div className="h-8 w-28 rounded bg-border" />
        <div className="h-4 w-2/3 max-w-md rounded bg-border/70" />
      </div>
      <div
        className="h-32 rounded-panel border border-dashed border-border bg-surface"
        aria-hidden="true"
      />
      <DatasetTableSkeleton />
    </div>
  );
}
