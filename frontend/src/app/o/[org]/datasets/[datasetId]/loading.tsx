export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="space-y-8">
      <div className="space-y-3" aria-hidden="true">
        <div className="h-4 w-24 rounded bg-border/70" />
        <div className="h-8 w-1/2 max-w-sm rounded bg-border" />
        <div className="h-4 w-2/3 max-w-md rounded bg-border/70" />
      </div>
      <div
        className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]"
        aria-hidden="true"
      >
        <div className="h-48 animate-pulse rounded-panel border border-border bg-surface motion-reduce:animate-none" />
        <div className="h-32 animate-pulse rounded-panel border border-border bg-surface motion-reduce:animate-none" />
      </div>
    </div>
  );
}
