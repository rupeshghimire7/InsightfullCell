export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="space-y-8">
      <div className="space-y-3" aria-hidden="true">
        <div className="h-4 w-20 rounded bg-border/70" />
        <div className="h-8 w-1/2 max-w-sm rounded bg-border" />
      </div>
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]" aria-hidden="true">
        <div className="space-y-3 rounded-panel border border-border bg-surface p-5">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex gap-4">
              <div className="size-5 animate-pulse rounded-full bg-border motion-reduce:animate-none" />
              <div className="h-4 flex-1 animate-pulse rounded bg-border motion-reduce:animate-none" />
            </div>
          ))}
        </div>
        <div className="h-40 animate-pulse rounded-panel bg-border/40 motion-reduce:animate-none" />
      </div>
    </div>
  );
}
