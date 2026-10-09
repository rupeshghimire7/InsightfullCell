import { DatasetTableSkeleton } from "@/components/datasets/dataset-table";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="space-y-8">
      <div className="space-y-2" aria-hidden="true">
        <div className="h-8 w-24 rounded bg-border" />
        <div className="h-4 w-2/3 max-w-md rounded bg-border/70" />
      </div>
      <DatasetTableSkeleton rows={5} />
    </div>
  );
}
